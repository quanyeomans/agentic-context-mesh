"""kairix.knowledge.entities.enrich — Wikidata description enricher.

Closes #415: nothing in production was writing ``n.summary`` to entity
nodes despite the schema, store health check, and entity audit all
treating it as load-bearing data. This module fetches the canonical
Wikidata description for entities that already have a ``wikidata_qid``
and writes it back via ``SET n.summary = $summary``.

Companion to :mod:`kairix.knowledge.entities.validate` — validate
resolves names → qids; enrich resolves qids → descriptions. Both never
raise; failures populate ``error`` on the returned dataclass.

No API key required (Wikidata public REST API). Timeout: 10s.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

WIKIDATA_ENTITY_DATA_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
_DEFAULT_TIMEOUT = 10
_USER_AGENT = "kairix-entity-enricher/0.9 (https://github.com/three-cubes/kairix)"


@dataclass(frozen=True)
class EntitySummary:
    """Result of a Wikidata description fetch for a single qid."""

    qid: str
    label: str
    description: str  # Canonical short description; empty when not available
    source: str = "wikidata"


@dataclass(frozen=True)
class EnrichResult:
    """Outcome of enriching one entity.

    ``updated`` is True iff a non-empty description was written to the
    Neo4j node. ``error`` is non-empty when the fetch or write failed —
    the caller iterates without halting on individual failures.
    """

    name: str
    qid: str = ""
    description: str = ""
    updated: bool = False
    skipped_reason: str = ""  # "no_qid" | "no_description" | "already_summary" | ""
    error: str = ""


@dataclass(frozen=True)
class EnrichBatchResult:
    """Outcome of enriching N entities in one pass."""

    requested: int
    updated: int
    skipped: int
    failed: int
    results: list[EnrichResult] = field(default_factory=list)
    error: str = ""


def fetch_wikidata_summary(
    qid: str,
    language: str = "en",
    http_get: Callable[..., requests.Response] | None = None,
) -> EntitySummary | None:
    """Fetch the canonical description for a Wikidata QID.

    Args:
        qid: Wikidata item ID (e.g. ``Q123456``).
        language: Language code for label + description lookup.
        http_get: Injectable HTTP GET for testing. Defaults to ``requests.get``.

    Returns:
        EntitySummary when the fetch succeeds and the qid resolves to an
        item with at least a label. ``None`` on network error, 404, 429,
        or malformed payload. Never raises.
    """
    if not qid:
        return None
    if http_get is None:
        http_get = requests.get

    url = WIKIDATA_ENTITY_DATA_URL.format(qid=qid)
    try:
        resp = http_get(url, timeout=_DEFAULT_TIMEOUT, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("fetch_wikidata_summary(%r): %s", qid, exc)
        return None

    entities = data.get("entities", {}) if isinstance(data, dict) else {}
    item = entities.get(qid)
    if not isinstance(item, dict):
        return None

    labels_raw = item.get("labels")
    descs_raw = item.get("descriptions")
    labels: dict[str, Any] = labels_raw if isinstance(labels_raw, dict) else {}
    descs: dict[str, Any] = descs_raw if isinstance(descs_raw, dict) else {}
    label_entry_raw = labels.get(language)
    desc_entry_raw = descs.get(language)
    label_entry: dict[str, Any] = label_entry_raw if isinstance(label_entry_raw, dict) else {}
    desc_entry: dict[str, Any] = desc_entry_raw if isinstance(desc_entry_raw, dict) else {}

    label = str(label_entry.get("value", ""))
    description = str(desc_entry.get("value", ""))

    if not label and not description:
        return None
    return EntitySummary(qid=qid, label=label, description=description)


def enrich_entity(
    name: str,
    neo4j_client: Any,
    *,
    overwrite: bool = False,
    http_get: Callable[..., requests.Response] | None = None,
) -> EnrichResult:
    """Fetch a Wikidata description for ``name`` and SET ``n.summary``.

    The entity must already have ``wikidata_qid`` populated (typically by
    a prior ``kairix entity validate --update`` pass). When ``overwrite``
    is False, entities with a non-empty existing ``summary`` are skipped
    (idempotency for re-runs).

    Args:
        name: Entity name as stored in Neo4j.
        neo4j_client: any client exposing ``cypher(query, params)`` returning
            list[dict]. Duck-typed; same shape as
            :mod:`kairix.knowledge.entities.validate`.
        overwrite: when True, replace existing summary even when populated.
        http_get: injectable HTTP GET seam for tests.

    Returns:
        EnrichResult — never raises. ``error`` carries any fetch/write failure;
        ``skipped_reason`` distinguishes the no-op branches.
    """
    try:
        rows = neo4j_client.cypher(
            "MATCH (n {name: $name}) RETURN n.wikidata_qid AS qid, n.summary AS summary LIMIT 1",
            {"name": name},
        )
    except Exception as exc:
        logger.warning("enrich_entity(%r): Neo4j lookup failed: %s", name, exc)
        return EnrichResult(name=name, error=f"neo4j_lookup_failed: {exc}")

    if not rows:
        return EnrichResult(name=name, skipped_reason="not_found")

    row = rows[0]
    qid = str(row.get("qid") or "")
    existing_summary = str(row.get("summary") or "")
    if not qid:
        return EnrichResult(name=name, skipped_reason="no_qid")
    if existing_summary and not overwrite:
        return EnrichResult(
            name=name,
            qid=qid,
            description=existing_summary,
            skipped_reason="already_summary",
        )

    summary = fetch_wikidata_summary(qid, http_get=http_get)
    if summary is None:
        return EnrichResult(name=name, qid=qid, error="wikidata_fetch_failed")
    if not summary.description:
        return EnrichResult(
            name=name,
            qid=qid,
            skipped_reason="no_description",
        )

    try:
        # The SET routes to a WRITE session automatically (cypher() derives the
        # access mode from the query — #416). The RETURN clause also verifies the
        # write landed; without it a no-op MATCH (entity not found) would silently
        # report updated=True.
        rows = neo4j_client.cypher(
            "MATCH (n {name: $name}) SET n.summary = $summary, n.summary_source = $source RETURN n.name AS name",
            {"name": name, "summary": summary.description, "source": summary.source},
        )
    except Exception as exc:
        logger.warning("enrich_entity(%r): Neo4j write failed: %s", name, exc)
        return EnrichResult(name=name, qid=qid, description=summary.description, error=f"neo4j_write_failed: {exc}")

    if not rows:
        logger.warning("enrich_entity(%r): MATCH returned 0 rows — entity not found after lookup race?", name)
        return EnrichResult(name=name, qid=qid, description=summary.description, error="neo4j_write_no_match")

    logger.info("enrich_entity: set summary on %s (qid=%s, %d chars)", name, qid, len(summary.description))
    return EnrichResult(name=name, qid=qid, description=summary.description, updated=True)


def enrich_all_missing(
    neo4j_client: Any,
    *,
    limit: int = 100,
    overwrite: bool = False,
    http_get: Callable[..., requests.Response] | None = None,
    progress_cb: Callable[[int, int, EnrichResult], None] | None = None,
) -> EnrichBatchResult:
    """Iterate every entity with a ``wikidata_qid`` but missing summary, enrich each.

    Bounded by ``limit`` so a single invocation can't run away. Stable
    ordering (by name) so repeated runs converge predictably.

    Args:
        neo4j_client: same duck-typed shape as :func:`enrich_entity`.
        limit: max number of candidates to process (>=1).
        overwrite: when True, also re-enrich entities that already have a summary.
        http_get: injectable HTTP GET seam for tests.
        progress_cb: optional ``(index_0based, total, result)`` callback;
            useful for CLI progress bars.

    Returns:
        EnrichBatchResult counting updated/skipped/failed buckets. Never raises.
    """
    if limit < 1:
        return EnrichBatchResult(requested=0, updated=0, skipped=0, failed=0, error="limit must be >= 1")

    summary_predicate = "" if overwrite else "AND (n.summary IS NULL OR n.summary = '') "
    try:
        rows = neo4j_client.cypher(
            "MATCH (n) "
            "WHERE n.wikidata_qid IS NOT NULL AND n.wikidata_qid <> '' "
            f"{summary_predicate}"
            "RETURN n.name AS name "
            "ORDER BY n.name "
            "LIMIT $limit",
            {"limit": limit},
        )
    except Exception as exc:
        logger.warning("enrich_all_missing: candidate query failed: %s", exc)
        return EnrichBatchResult(requested=0, updated=0, skipped=0, failed=0, error=f"candidate_query_failed: {exc}")

    candidates = [str(r.get("name") or "") for r in rows if r.get("name")]
    total = len(candidates)
    results: list[EnrichResult] = []
    updated_n = 0
    skipped_n = 0
    failed_n = 0

    for i, name in enumerate(candidates):
        result = enrich_entity(name, neo4j_client, overwrite=overwrite, http_get=http_get)
        results.append(result)
        if result.updated:
            updated_n += 1
        elif result.error:
            failed_n += 1
        else:
            skipped_n += 1
        if progress_cb is not None:
            progress_cb(i, total, result)

    return EnrichBatchResult(
        requested=total,
        updated=updated_n,
        skipped=skipped_n,
        failed=failed_n,
        results=results,
    )
