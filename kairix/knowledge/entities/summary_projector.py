"""Entity-summary projector — ADR-036 §Mechanics (Issue #460, Slice B).

Public surface:

  * :class:`EntitySummaryProjectorImpl` — the real projector
    implementation
  * :class:`EntitySummaryProjectorDeps` — F6-clean injection seam
  * :func:`run_entity_summary_projector_tick` — flag-gated tick
    dispatcher the worker loop composes (continuous cadence wiring
    lands alongside the Slice C E2E composed-path)



The :class:`EntitySummaryProjectorImpl` reads pending entities from
Neo4j (``n.summary`` populated, hash mismatched or never indexed),
writes one chunk per entity into the synthetic ``entity-summaries``
collection via the canonical
:class:`~kairix.core.protocols.ChunkWriter` seam, then marks each
entity ``n.summary_indexed_at`` in Neo4j on success.

Failure isolation: per-entity write failures are logged at WARN and
counted via :attr:`EntitySummaryProjectionResult.failed`; the rest of
the tick continues. A Neo4j poll failure produces an idle result
(``projected=0``, ``failed=0``) — the worker boundary decides whether
to surface based on telemetry, not by absorbing the wrong failure.

ADR-036 §Q6 idempotency contract: a re-tick with no Neo4j changes
projects zero new chunks because the hash filter short-circuits each
row. A re-projection (summary text changed) deletes the prior chunk
via :meth:`ChunkWriter.delete_by_source_uri` before upserting, so the
new ``content_hash`` doesn't leave a stale row behind.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kairix.core.protocols import (
    Chunk,
    EntitySummaryProjectionResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cypher statements
# ---------------------------------------------------------------------------

# F63-bounded: LIMIT $per_tick_max_items declared inline. The worker stage
# threads the same per_tick_max_items value declared at construction time
# (F66) so the bound is operator-visible.
_POLL_CYPHER = """
MATCH (n)
WHERE n.summary IS NOT NULL AND n.summary <> ''
RETURN n.name AS name,
       n.wikidata_qid AS qid,
       n.summary AS summary,
       n.summary_indexed_content_hash AS prior_hash,
       n.summary_source AS summary_source
LIMIT $per_tick_max_items
"""

_MARK_INDEXED_CYPHER = """
MATCH (n {name: $name})
SET n.summary_indexed_at = $now,
    n.summary_indexed_content_hash = $hash
RETURN n.name AS name
"""


# ---------------------------------------------------------------------------
# Helpers (public so tests can drive them directly; F5 clean)
# ---------------------------------------------------------------------------


def hash_summary(summary: str) -> str:
    """SHA-256 hex digest of ``summary`` — used as both the chunk's
    ``content_hash`` and Neo4j's ``n.summary_indexed_content_hash``.

    Same string → same digest, so re-running a tick with no changes
    short-circuits via the prior-hash equality check below.
    """
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


def build_entity_summary_chunk(
    *,
    summary: str,
    qid: str,
    name: str,
    tick_iso: str,
    content_hash: str,
) -> Chunk:
    """Build the canonical :class:`Chunk` for one entity summary.

    F39: ``sensitivity="public"`` is declared via the synthetic
    ``wikidata`` connector-config entry (operator overlay landed
    alongside this slice). The chunker namespace
    ``entity-summary:v1`` is the F55 chunker-version stamp so a
    future re-chunk sweep can filter the affected corpus by stamp.
    """
    return Chunk(
        text=summary,
        content_hash=content_hash,
        source_name="wikidata",
        source_uri=f"entity://{qid}",
        source_modified_at=tick_iso,
        source_page=None,
        sensitivity="public",
        chunker_version="entity-summary:v1",
        tags=("entity-summary", f"qid:{qid}"),
        metadata={"entity_name": name, "wikidata_qid": qid},
    )


def now_iso() -> str:
    """Return the current UTC time as a Zulu ISO-8601 string.

    Wrapped in a helper so the projector can override it for tests
    via :class:`EntitySummaryProjectorImpl`'s ``clock`` kwarg.

    Public so tests can drive it directly (F5-clean) — no underscore
    prefix means callers can compose around it.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_now_iso = now_iso  # Backwards-compatible alias for any internal callers.


# ---------------------------------------------------------------------------
# Projector implementation
# ---------------------------------------------------------------------------


class EntitySummaryProjectorImpl:
    """Real :class:`EntitySummaryProjector` (ADR-036, Slice B).

    Composes a Neo4j client + a :class:`ChunkWriter` (typically the
    canonical ``_SqliteChunkWriter`` routed via :func:`legacy_chunk_writer`
    at construction time). The worker-tick stage instantiates one of
    these at startup and calls :meth:`tick` once per tick window when
    ``entity_summary_indexing_enabled`` is ON.

    Construction:
      * ``neo4j`` — any object exposing ``cypher(query, params) -> list[dict]``
      * ``chunk_writer`` — a :class:`ChunkWriter` Protocol implementation
      * ``clock`` — optional zero-arg callable returning the tick-start
        ISO string. Default is :func:`_now_iso`; tests inject a fixed
        timestamp so the chunk's ``source_modified_at`` is deterministic.

    Failure isolation: per-entity failures (chunk-write raise, Neo4j
    mark-indexed raise) are logged at WARN and counted; the tick
    never raises.
    """

    def __init__(
        self,
        *,
        neo4j: Any,
        chunk_writer: Any,
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self._neo4j = neo4j
        self._chunk_writer = chunk_writer
        self._clock = clock

    def tick(self, *, per_tick_max_items: int = 200) -> EntitySummaryProjectionResult:
        rows = self._fetch_pending(per_tick_max_items)
        if not rows:
            return EntitySummaryProjectionResult()

        tick_iso = str(self._clock())
        projected = updated = skipped = failed = 0
        for row in rows:
            try:
                outcome = self._process_one(row, tick_iso=tick_iso)
            except Exception as exc:
                name = str(row.get("name") or "?")
                logger.warning(
                    "EntitySummaryProjector: per-entity tick failed for %s — %s",
                    name,
                    exc,
                )
                failed += 1
                continue
            if outcome == "projected":
                projected += 1
            elif outcome == "updated":
                updated += 1
            elif outcome == "skipped":
                skipped += 1
        return EntitySummaryProjectionResult(
            projected=projected,
            updated=updated,
            skipped=skipped,
            failed=failed,
        )

    def _fetch_pending(self, per_tick_max_items: int) -> list[Mapping[str, Any]]:
        try:
            return list(
                self._neo4j.cypher(
                    _POLL_CYPHER,
                    {"per_tick_max_items": int(per_tick_max_items)},
                )
            )
        except Exception as exc:
            logger.warning("EntitySummaryProjector: Neo4j poll failed — %s", exc)
            return []

    def _process_one(self, row: Mapping[str, Any], *, tick_iso: str) -> str:
        """Project one entity row. Returns the outcome label.

        Outcomes:
          * ``"projected"`` — net-new chunk written + entity marked
            indexed for the first time
          * ``"updated"`` — prior chunk existed for this entity, was
            deleted, and a new chunk written for the changed summary
          * ``"skipped"`` — already indexed under the same hash, or
            malformed row (missing summary / qid / name)
        """
        summary = str(row.get("summary") or "")
        qid = str(row.get("qid") or "")
        name = str(row.get("name") or "")
        prior_hash = str(row.get("prior_hash") or "")

        if not summary or not qid or not name:
            return "skipped"

        current_hash = hash_summary(summary)
        if prior_hash == current_hash:
            return "skipped"

        chunk = build_entity_summary_chunk(
            summary=summary,
            qid=qid,
            name=name,
            tick_iso=tick_iso,
            content_hash=current_hash,
        )

        if prior_hash:
            # Re-projection: drop the prior chunk so the new content_hash
            # doesn't leave a stale row behind. Idempotent on the
            # never-projected branch via the prior_hash truthiness check.
            self._chunk_writer.delete_by_source_uri(f"entity://{qid}")
        self._chunk_writer.upsert([chunk])

        self._mark_indexed(name=name, content_hash=current_hash, tick_iso=tick_iso)
        return "updated" if prior_hash else "projected"

    def _mark_indexed(self, *, name: str, content_hash: str, tick_iso: str) -> None:
        """Stamp ``n.summary_indexed_at`` + ``n.summary_indexed_content_hash``.

        Same try-block discipline as the rest of ``_process_one`` —
        if Neo4j fails here, the surrounding ``except`` in :meth:`tick`
        catches and increments ``failed``. The chunk stays written
        (idempotent next tick via content_hash) but the entity will
        re-project until Neo4j recovers.
        """
        self._neo4j.cypher(
            _MARK_INDEXED_CYPHER,
            {"name": name, "now": tick_iso, "hash": content_hash},
        )


# ---------------------------------------------------------------------------
# Flag-gated tick dispatcher — composed by the worker loop (ADR-036 §Worker)
# ---------------------------------------------------------------------------


def default_flag_reader() -> bool:
    """Default flag-reader for production wiring — reads the canonical
    feature-flag registry value for ``entity_summary_indexing_enabled``.

    Production callers omit the deps and get this; tests pass a
    pinned-bool lambda to drive the OFF / ON branches without
    monkey-patching the resolver. F1/F2/F5 clean — public surface so
    tests can drive it directly without underscore-prefixed imports.
    """
    from kairix.core.features.resolver import flag

    return flag("entity_summary_indexing_enabled")


@dataclass
class EntitySummaryProjectorDeps:
    """F6-clean injection seam for :func:`run_entity_summary_projector_tick`.

    F66-compliant: ``per_tick_max_items`` is declared here so the
    operator-visible cap travels with the deps. The worker stage's
    ``disk_watermark_min_free_bytes`` is shared with every other tick
    stage via the worker-level config (not duplicated per-stage).

    Fields:

      * ``flag_reader`` — returns ``True`` iff
        ``entity_summary_indexing_enabled`` is ON. Defaults to the
        production resolver. Tests pass a lambda returning a
        deterministic bool.
      * ``projector_factory`` — zero-arg builder returning a fully-wired
        :class:`EntitySummaryProjectorImpl`. Production wires Neo4j +
        ``legacy_chunk_writer`` against the live SQLite. Tests pass a
        factory returning a projector built with fakes.
      * ``per_tick_max_items`` — F66 per-tick cap. Default 200 matches
        ADR-036 §Worker. Operators tune in
        ``kairix.config.yaml`` (next-slice wiring).
    """

    flag_reader: Callable[[], bool] = field(default_factory=lambda: default_flag_reader)
    projector_factory: Callable[[], EntitySummaryProjectorImpl] = field(
        default_factory=lambda: default_projector_builder
    )
    per_tick_max_items: int = 200


class UnavailableNeo4jClient:
    """Placeholder Neo4j client whose ``cypher`` always raises.

    Slice B's :func:`default_projector_builder` wires this so the
    flag-gated tick dispatcher's poll path absorbs the failure into
    an idle result. Slice C+ overrides the default factory with a
    real Neo4j client + a real ChunkWriter so the projector runs
    against the live worker DB.
    """

    def cypher(self, _query: str, _params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise RuntimeError("EntitySummaryProjector: no Neo4j wired — Slice C+ deploys the live factory")


class NoopChunkWriter:
    """Placeholder ChunkWriter satisfying the Protocol with all-zero returns.

    Pairs with :class:`UnavailableNeo4jClient` in
    :func:`default_projector_builder`. Never invoked in normal Slice B
    flow (Neo4j poll raises first), but kept Protocol-compatible so a
    future caller wiring a real Neo4j against this default writer
    stays safe.
    """

    def upsert(self, _chunks: Any) -> int:
        return 0

    def delete_by_source_uri(self, _source_uri: str) -> int:
        return 0


def default_projector_builder() -> EntitySummaryProjectorImpl:
    """Production default factory placeholder (Slice B).

    Returns a projector with no Neo4j connection — the tick path
    returns an idle result via the projector's poll-failure path.
    The continuous worker-loop wiring (Slice C+) overrides this
    default with a builder that wires the live Neo4j client +
    ``legacy_chunk_writer`` against the worker DB.

    Public so :class:`EntitySummaryProjectorDeps` can reference it
    via ``field(default_factory=...)``; F1/F2/F6 clean.
    """
    return EntitySummaryProjectorImpl(neo4j=UnavailableNeo4jClient(), chunk_writer=NoopChunkWriter())


def run_entity_summary_projector_tick(
    deps: EntitySummaryProjectorDeps | None = None,
) -> EntitySummaryProjectionResult | None:
    """Run one flag-gated entity-summary projector tick.

    Returns the :class:`EntitySummaryProjectionResult` envelope when the
    flag is ON, or ``None`` when the flag is OFF (structural no-op).
    Per ADR-036 §Cutover the OFF branch MUST be a byte-for-byte
    no-op so flipping the flag is reversible.

    The continuous worker-loop dispatch (cadence + state persistence)
    lands alongside the Slice C E2E composed-path PR (#461); this
    function is the operator-visible call point both Slice C's E2E
    and the worker loop will compose.
    """
    deps = deps if deps is not None else EntitySummaryProjectorDeps()
    if not deps.flag_reader():
        return None
    projector = deps.projector_factory()
    return projector.tick(per_tick_max_items=deps.per_tick_max_items)
