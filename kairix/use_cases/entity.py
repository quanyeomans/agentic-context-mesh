"""Entity use cases — entity suggest + entity validate.

Phase 3b of the CLI/MCP feature parity initiative (#168). Pre-Phase-3b
both operations were CLI-only; agents needed to shell out to extract
entities from prose or validate them against Wikidata. This module
absorbs the per-operation logic into use cases returning uniform
dataclasses; both adapters serialise from them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# F17 — Mention attribute names shared across dict ingest, attribute reflection,
# and serialisation paths; extract so a rename hits a single edit site.
_KEY_DESCRIPTION = "description"
_KEY_CONFIDENCE = "confidence"


def _default_neo4j_client() -> Any:
    from kairix.knowledge.graph.client import get_client

    return get_client()


def production_suggest(
    text: str,
    neo4j_client: Any,
    *,
    overrides_path: Path | None = None,
) -> list[Any]:
    """Production suggest wrapper — loads the vault override file once.

    Reads ``${KAIRIX_DOCUMENT_ROOT}/04-Agent-Knowledge/_entity-overrides.md``
    (or the ``KAIRIX_ENTITY_OVERRIDES_PATH`` operator override), builds
    the filter chain with the resulting allowlist + label overrides,
    and hands it to ``suggest_entities``. Missing / malformed override
    file falls back to the default chain — never blocks. Closes #166.

    ``overrides_path`` is a deployment-time seam: operators or callers
    that resolve the override file themselves (CLI flag, in-memory
    config) pass it in here. Production callers leave it ``None`` and
    the canonical path resolves from ``kairix.paths.entity_overrides_path``.
    F6-clean — the kwarg has a production use, not just test ergonomics.
    """
    from kairix.knowledge.entities.filters import default_suggestion_filter_chain
    from kairix.knowledge.entities.overrides import load_entity_overrides
    from kairix.knowledge.entities.suggest import suggest_entities
    from kairix.paths import entity_overrides_path

    path = overrides_path if overrides_path is not None else entity_overrides_path()
    overrides = load_entity_overrides(path)
    chain = default_suggestion_filter_chain(
        allowlist=overrides.allowlist,
        person_overrides=overrides.person_overrides,
        org_overrides=overrides.org_overrides,
    )
    return suggest_entities(text, neo4j_client, filter_chain=chain)


def _default_validate(name: str, neo4j_client: Any, update: bool) -> dict[str, Any]:
    from kairix.knowledge.entities.validate import validate_entity

    return validate_entity(name, neo4j_client, update=update)


# ---------------------------------------------------------------------------
# entity_suggest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SuggestedEntityHit:
    """A single NER suggestion projected from ``SuggestedEntity``."""

    text: str
    label: str
    is_new: bool
    existing_id: str = ""
    existing_name: str = ""
    context: str = ""


@dataclass(frozen=True)
class EntitySuggestOutput:
    text: str
    suggestions: list[SuggestedEntityHit] = field(default_factory=list)
    new_count: int = 0
    existing_count: int = 0
    error: str = ""


@dataclass(frozen=True)
class EntitySuggestDeps:
    """Injectable dependencies for ``run_entity_suggest``.

    Mirrors ``WorkerDeps`` (kairix/worker.py): each callable is
    non-Optional with a ``default_factory`` returning the production
    helper. Tests pass concrete fakes; production callers leave
    ``deps=None``.
    """

    suggest_fn: Callable[..., list[Any]] = field(default_factory=lambda: production_suggest)
    neo4j_client_fn: Callable[[], Any] = field(default_factory=lambda: _default_neo4j_client)


def _project_suggestion(s: Any) -> SuggestedEntityHit:
    return SuggestedEntityHit(
        text=str(getattr(s, "text", "")),
        label=str(getattr(s, "label", "")),
        is_new=bool(getattr(s, "is_new", False)),
        existing_id=str(getattr(s, "existing_id", "") or ""),
        existing_name=str(getattr(s, "existing_name", "") or ""),
        context=str(getattr(s, "context", "")),
    )


def run_entity_suggest(
    text: str,
    *,
    deps: EntitySuggestDeps | None = None,
) -> EntitySuggestOutput:
    """Run NER over ``text`` and cross-reference with Neo4j.

    Never raises — failures populate ``error``.
    """
    d = deps or EntitySuggestDeps()

    try:
        neo4j = d.neo4j_client_fn()
        raw = d.suggest_fn(text, neo4j)
        hits = [_project_suggestion(s) for s in raw]
        new_count = sum(1 for h in hits if h.is_new)
        return EntitySuggestOutput(
            text=text,
            suggestions=hits,
            new_count=new_count,
            existing_count=len(hits) - new_count,
        )
    except ImportError as exc:
        # spaCy missing — operator-actionable.
        return EntitySuggestOutput(
            text=text,
            error=f"ImportError: {exc}. Install with: pip install 'kairix[nlp]'",
        )
    except Exception as exc:
        logger.warning("run_entity_suggest failed: %s", exc, exc_info=True)
        return EntitySuggestOutput(text=text, error=f"{type(exc).__name__}: {exc}")


def entity_suggest_output_to_envelope(out: EntitySuggestOutput) -> dict[str, Any]:
    return {
        "text": out.text,
        "suggestions": [
            {
                "text": h.text,
                "label": h.label,
                "is_new": h.is_new,
                "existing_id": h.existing_id,
                "existing_name": h.existing_name,
                "context": h.context,
            }
            for h in out.suggestions
        ],
        "new_count": out.new_count,
        "existing_count": out.existing_count,
        "error": out.error,
    }


# ---------------------------------------------------------------------------
# entity_validate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityValidateMatch:
    qid: str
    label: str
    description: str
    url: str
    confidence: str  # high | medium | low


@dataclass(frozen=True)
class EntityValidateOutput:
    name: str
    neo4j_id: str = ""
    matches: list[EntityValidateMatch] = field(default_factory=list)
    updated: bool = False
    error: str = ""


@dataclass(frozen=True)
class EntityValidateDeps:
    """Injectable dependencies for ``run_entity_validate``.

    Mirrors ``WorkerDeps``: ``validate_fn`` and ``neo4j_client_fn``
    are non-Optional with ``default_factory`` wiring the production
    helpers.
    """

    validate_fn: Callable[..., dict[str, Any]] = field(default_factory=lambda: _default_validate)
    neo4j_client_fn: Callable[[], Any] = field(default_factory=lambda: _default_neo4j_client)


def _project_match(m: Any) -> EntityValidateMatch:
    if isinstance(m, dict):
        return EntityValidateMatch(
            qid=str(m.get("qid", "")),
            label=str(m.get("label", "")),
            description=str(m.get(_KEY_DESCRIPTION, "")),
            url=str(m.get("url", "")),
            confidence=str(m.get(_KEY_CONFIDENCE, "")),
        )
    return EntityValidateMatch(
        qid=str(getattr(m, "qid", "")),
        label=str(getattr(m, "label", "")),
        description=str(getattr(m, _KEY_DESCRIPTION, "")),
        url=str(getattr(m, "url", "")),
        confidence=str(getattr(m, _KEY_CONFIDENCE, "")),
    )


def run_entity_validate(
    name: str,
    *,
    update: bool = False,
    deps: EntityValidateDeps | None = None,
) -> EntityValidateOutput:
    """Validate ``name`` against Wikidata and optionally update Neo4j.

    Never raises — failures populate ``error``.
    """
    d = deps or EntityValidateDeps()

    try:
        neo4j = d.neo4j_client_fn()
        result = d.validate_fn(name, neo4j, update=update)
        matches = [_project_match(m) for m in result.get("matches", [])]
        return EntityValidateOutput(
            name=str(result.get("name", name)),
            neo4j_id=str(result.get("neo4j_id") or ""),
            matches=matches,
            updated=bool(result.get("updated", False)),
            error=str(result.get("error", "") or ""),
        )
    except Exception as exc:
        logger.warning("run_entity_validate failed: %s", exc, exc_info=True)
        return EntityValidateOutput(name=name, error=f"{type(exc).__name__}: {exc}")


def entity_validate_output_to_envelope(out: EntityValidateOutput) -> dict[str, Any]:
    return {
        "name": out.name,
        "neo4j_id": out.neo4j_id,
        "matches": [
            {
                "qid": m.qid,
                "label": m.label,
                _KEY_DESCRIPTION: m.description,
                "url": m.url,
                _KEY_CONFIDENCE: m.confidence,
            }
            for m in out.matches
        ],
        "updated": out.updated,
        "error": out.error,
    }


# ---------------------------------------------------------------------------
# entity_enrich (#415 — write Wikidata descriptions to n.summary)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityEnrichOutput:
    """Use-case output for a single ``kairix entity enrich`` invocation.

    Mirrors the per-entity ``EnrichResult`` shape with ``error`` for the
    use-case-level boundary (e.g. neo4j_client_fn raised).
    """

    name: str
    qid: str = ""
    description: str = ""
    updated: bool = False
    skipped_reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class EntityEnrichBatchOutput:
    """Use-case output for ``kairix entity enrich --all-missing``."""

    requested: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[EntityEnrichOutput] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class EntityEnrichDeps:
    """Injectable dependencies for ``run_entity_enrich``.

    Mirrors :class:`EntityValidateDeps` — defaults wire the production
    helpers; tests inject Fake* + a fake ``enrich_fn`` to avoid network.
    """

    enrich_fn: Callable[..., Any] = field(default_factory=lambda: _default_enrich)
    enrich_batch_fn: Callable[..., Any] = field(default_factory=lambda: _default_enrich_batch)
    neo4j_client_fn: Callable[[], Any] = field(default_factory=lambda: _default_neo4j_client)


def _default_enrich(name: str, neo4j_client: Any, *, overwrite: bool = False) -> Any:
    from kairix.knowledge.entities.enrich import enrich_entity

    return enrich_entity(name, neo4j_client, overwrite=overwrite)


def _default_enrich_batch(neo4j_client: Any, *, limit: int = 100, overwrite: bool = False) -> Any:
    from kairix.knowledge.entities.enrich import enrich_all_missing

    return enrich_all_missing(neo4j_client, limit=limit, overwrite=overwrite)


def _project_enrich(r: Any) -> EntityEnrichOutput:
    return EntityEnrichOutput(
        name=str(getattr(r, "name", "")),
        qid=str(getattr(r, "qid", "")),
        description=str(getattr(r, _KEY_DESCRIPTION, "")),
        updated=bool(getattr(r, "updated", False)),
        skipped_reason=str(getattr(r, "skipped_reason", "")),
        error=str(getattr(r, "error", "") or ""),
    )


def run_entity_enrich(
    name: str,
    *,
    overwrite: bool = False,
    deps: EntityEnrichDeps | None = None,
) -> EntityEnrichOutput:
    """Enrich a single entity by fetching its Wikidata description.

    Never raises — failures populate ``error``.
    """
    d = deps or EntityEnrichDeps()
    try:
        neo4j = d.neo4j_client_fn()
        result = d.enrich_fn(name, neo4j, overwrite=overwrite)
        return _project_enrich(result)
    except Exception as exc:
        logger.warning("run_entity_enrich failed: %s", exc, exc_info=True)
        return EntityEnrichOutput(name=name, error=f"{type(exc).__name__}: {exc}")


def run_entity_enrich_batch(
    *,
    limit: int = 100,
    overwrite: bool = False,
    deps: EntityEnrichDeps | None = None,
) -> EntityEnrichBatchOutput:
    """Enrich every entity with a wikidata_qid but missing summary, up to ``limit``.

    Never raises — failures populate ``error`` on the batch output.
    """
    d = deps or EntityEnrichDeps()
    try:
        neo4j = d.neo4j_client_fn()
        batch = d.enrich_batch_fn(neo4j, limit=limit, overwrite=overwrite)
        results = [_project_enrich(r) for r in getattr(batch, "results", [])]
        return EntityEnrichBatchOutput(
            requested=int(getattr(batch, "requested", 0)),
            updated=int(getattr(batch, "updated", 0)),
            skipped=int(getattr(batch, "skipped", 0)),
            failed=int(getattr(batch, "failed", 0)),
            results=results,
            error=str(getattr(batch, "error", "") or ""),
        )
    except Exception as exc:
        logger.warning("run_entity_enrich_batch failed: %s", exc, exc_info=True)
        return EntityEnrichBatchOutput(error=f"{type(exc).__name__}: {exc}")


def entity_enrich_output_to_envelope(out: EntityEnrichOutput) -> dict[str, Any]:
    return {
        "name": out.name,
        "qid": out.qid,
        _KEY_DESCRIPTION: out.description,
        "updated": out.updated,
        "skipped_reason": out.skipped_reason,
        "error": out.error,
    }


def entity_enrich_batch_to_envelope(out: EntityEnrichBatchOutput) -> dict[str, Any]:
    return {
        "requested": out.requested,
        "updated": out.updated,
        "skipped": out.skipped,
        "failed": out.failed,
        "results": [entity_enrich_output_to_envelope(r) for r in out.results],
        "error": out.error,
    }
