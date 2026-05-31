"""Background worker for scheduled tasks.

Runs inside the kairix-worker Docker container. Handles:
- Incremental document indexing (every hour)
- Entity relationship seeding (once a day at 3am)
- Health check logging (every 6 hours)

Usage:
    python -m kairix.worker
    # Or via Docker: docker compose exec kairix-worker worker
"""

from __future__ import annotations

import logging
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from kairix.paths import (
    connector_sync_disabled,
    data_dir,
    document_root,
    maintenance_interval_seconds,
    maintenance_retention_days,
    maintenance_skip_noop_threshold,
    preflight_strict,
    worker_pause_flag_path,
    worker_state_path,
)
from kairix.worker_state import WorkerPhase, WorkerState, read_state, write_state

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kairix.core.embed.use_cases import EmbedPipelineResult
    from kairix.core.protocols import Chunk, EntitySignal

logger = logging.getLogger(__name__)

# #224 phase 4 — pause-flag polling cadence.
# When the worker is in PAUSED phase, it sleeps this long between flag
# re-checks. Short enough that operators see resumption quickly (CLI tells
# them "may take up to 5s"), long enough not to thrash on a touch-file
# stat call. Exposed as a module constant so tests can run a couple of
# iterations through the pause-check without injecting the value.
PAUSE_POLL_INTERVAL_S = 5

# Task schedule (seconds between runs)
EMBED_INTERVAL = 3600  # 1 hour
ENTITY_SEED_INTERVAL = 86400  # 24 hours
HEALTH_CHECK_INTERVAL = 21600  # 6 hours
WIKILINKS_INTERVAL = 3600  # 1 hour — runs after embed; --changed mtime-filters
# SC-6 connector-framework seam — the worker tick that drives every
# registered SourceConnector through list_changes → fetch → bronze →
# silver → cursor.advance. 900s (15 min) is the Wave-1 default: short
# enough to feel responsive on a webhook-less source (Notion polling),
# long enough not to thrash Graph delta-tokens on quieter sources.
# Wave 2 fills in the body inside kairix/core/connectors/; Wave 1 only
# wires the dispatch slot. See docs/architecture/connector-ingestion-architecture.md §6.
CONNECTOR_SYNC_INTERVAL = 900  # 15 minutes
# Dispatch-table key for the connector-sync slot. Held as a constant
# rather than inline so the maintenance-cycle dispatch list, the per-task
# timestamp dict, and the return tuple stay in lock-step (F17).
_CONNECTOR_SYNC_KEY = "connector_sync"

# GH #334 — Neo4j entity-graph drain cadence. 600s (10 min) drains
# 3000 rows/hour at the default 500-row batch — fast enough that
# fresh signals reach Neo4j within ~30 min, slow enough that a
# transient Neo4j outage retries gracefully without thrashing.
# Operators with a large historical backlog run
# ``kairix curator drain --batch-size 5000 --max-batches 100``
# manually; the unattended cadence below protects the worker loop.
NEO4J_DRAIN_INTERVAL = 600  # 10 minutes

# Idle backoff (#224): when embed runs find no work to do, the next-embed
# wait extends exponentially. Cap at 4 hours so we don't go totally silent
# on a long-idle vault but also don't churn CPU/IO every hour for nothing.
EMBED_BACKOFF_NOOP_THRESHOLD = 2  # after N consecutive no-ops, start backing off
EMBED_BACKOFF_MAX_INTERVAL = 14400  # 4 hours — cap on backed-off embed interval

# #224 phase 2 — maintenance-skip threshold.
# When the embed no-op streak hits this count, the three maintenance scans
# (entity_seed, health_check, wikilinks_inject) become pointless work and
# the worker skips them too until embed next finds work. Resolved at module
# import time from KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD via paths.py
# (F4 — env reads stay centralised). Threshold tuned to default 10 so the
# embed-backoff exponential has time to slow polling down before we silence
# maintenance, but operators can lower it on tiny shared hosts.
MAINTENANCE_SKIP_NOOP_THRESHOLD = maintenance_skip_noop_threshold()


def _default_embed() -> EmbedPipelineResult:
    """Default embed implementation — runs the embed use case directly.

    Returns the structured ``EmbedPipelineResult`` so the worker can log
    structured outcomes (embed counts, recall score, alerts) without
    depending on CLI exit-code semantics. Critically, this DOES NOT call
    the CLI ``main()`` — that path raises ``SystemExit`` on recall-gate
    failures and would terminate the worker process. The use case raises
    only on truly unrecoverable conditions.
    """
    from kairix.core.embed.use_cases import run_incremental_embed_pipeline

    return run_incremental_embed_pipeline()


def _default_entity_seed() -> None:
    """Default entity seed implementation — lazy-imports and runs store crawl."""
    from kairix.knowledge.store.cli import main as store_main

    store_main(
        [
            "crawl",
            "--document-root",
            str(document_root()),
        ]
    )


def _default_wikilinks_inject() -> None:
    """Default wikilinks inject — runs ``kairix wikilinks inject --changed``.

    The CLI's ``main`` may raise ``SystemExit`` (e.g. when no entities
    are loaded yet, before the entity seed has run). The worker's
    ``run_wikilinks_inject`` catches that to keep the worker alive.
    """
    from kairix.knowledge.wikilinks.cli import main as wikilinks_main

    wikilinks_main(["inject", "--changed"])


def _default_health_check() -> list[Any]:
    """Default health check — lazy-imports and runs all deployment checks."""
    from kairix.platform.onboard.check import run_all_checks

    return run_all_checks()


@dataclass(frozen=True)
class ConnectorSyncResult:
    """Structured outcome of one connector-framework sync tick.

    Wave-1 placeholder shape. The worker logs these counters at INFO so
    operators can see end-to-end progress without grep-ing per-connector
    logs. Wave 2 (orchestration under ``kairix/core/connectors/``) will
    populate the fields from the real per-batch transaction.

    Fields:
        synced: items successfully written to Bronze and processed through
            Silver in this tick (cursor advanced past each).
        failed: items where ``fetch`` raised after the configured retry
            count — counted toward dead-letter on the next tick.
        dead_letter_added: items moved into the dead-letter table this
            tick (so operators can alert on a non-zero delta).
    """

    synced: int = 0
    failed: int = 0
    dead_letter_added: int = 0


class _SqliteChunkWriter:
    """Minimal in-process :class:`~kairix.core.protocols.ChunkWriter`.

    Wave-2 IM-3 keeps the worker independent from the legacy
    ``DocumentScanner`` writer surface — there is no production
    ``DocumentsTableWriter`` yet. This writer persists each
    :class:`~kairix.core.protocols.Chunk` against the canonical
    ``documents`` + ``content`` + ``content_vectors`` + ``documents_fts``
    tables using the same shared :class:`sqlite3.Connection` the pipeline
    drives, so the per-batch transaction stays atomic.

    The writer never commits — the caller's per-batch transaction owns
    the commit (matches :class:`FilesystemBronzeStore` discipline).

    FTS5 invariant: every chunk write also lands a ``documents_fts`` row
    so BM25 retrieval can find it. Without this, the hybrid ranker
    silently degrades to vector-only for new-path chunks. Contract test
    ``tests/contracts/test_chunk_writer_fts_invariant.py`` and integration
    test ``tests/integration/test_connector_search_round_trip.py`` pin
    the pairing.
    """

    def __init__(self, db: sqlite3.Connection, collection: str) -> None:
        self._db = db
        self._collection = collection

    def upsert(self, chunks: Sequence[Chunk]) -> int:
        """Persist ``chunks`` to documents + content + content_vectors + documents_fts.

        Each chunk lands as one ``documents`` row keyed by ``(collection,
        path=source_uri+seq)``, one ``content`` row keyed by
        ``content_hash``, one ``content_vectors`` row carrying the chunk
        sequence, AND one ``documents_fts`` row so BM25 search finds it.
        Does NOT commit.
        """
        written = 0
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for seq, chunk in enumerate(chunks):
            path = f"{chunk.source_uri}#{seq}"
            # Use UPSERT (ON CONFLICT DO UPDATE) rather than INSERT OR REPLACE.
            # INSERT OR REPLACE on a UNIQUE conflict DELETEs the old row and
            # INSERTs a new one — that allocates a fresh rowid, which orphans
            # the existing documents_fts row keyed by the old rowid. UPSERT
            # preserves the documents.id so the FTS row stays addressable.
            self._db.execute(
                "INSERT INTO documents "
                "(collection, path, hash, source_name, source_uri, "
                "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT (collection, path) DO UPDATE SET "
                "hash = excluded.hash, source_name = excluded.source_name, "
                "source_uri = excluded.source_uri, "
                "source_modified_at = excluded.source_modified_at, "
                "source_page = excluded.source_page, "
                "sensitivity = excluded.sensitivity, "
                "modified_at = excluded.modified_at, active = 1",
                (
                    self._collection,
                    path,
                    chunk.content_hash,
                    chunk.source_name,
                    chunk.source_uri,
                    chunk.source_modified_at,
                    chunk.source_page,
                    chunk.sensitivity,
                    now,
                    chunk.source_modified_at,
                ),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
                (chunk.content_hash, chunk.text, now),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
                (chunk.content_hash, seq, 0),
            )
            # FTS5 write — look up the stable documents.id via the unique key
            # (collection, path), then DELETE-then-INSERT the FTS row so the
            # match-text reflects the current chunk.text on update.
            row = self._db.execute(
                "SELECT id FROM documents WHERE collection = ? AND path = ?",
                (self._collection, path),
            ).fetchone()
            if row is not None:
                doc_id = row[0]
                self._db.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
                self._db.execute(
                    "INSERT INTO documents_fts (rowid, filepath, title, doc) VALUES (?, ?, ?, ?)",
                    (doc_id, path, "", chunk.text),
                )
            written += 1
        return written


class _SqliteEntityGraphSink:
    """Minimal in-process :class:`~kairix.core.protocols.EntityGraphSink`.

    Stages :class:`~kairix.core.protocols.EntitySignal` rows into the
    ``entity_signals`` table on the shared connection. A separate worker
    job (Curator-coupling boundary, Wave 3+) drains the table and pushes
    to Neo4j. Wave 2 only needs the staging side wired.

    Does NOT commit — the caller's per-batch transaction owns the commit.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def buffer(self, signals: Sequence[EntitySignal]) -> int:
        """Write entity signals to the ``entity_signals`` staging table."""
        staged = 0
        for sig in signals:
            self._db.execute(
                "INSERT INTO entity_signals "
                "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (sig.kind, sig.value, sig.source_uri, sig.modified_at, sig.confidence, sig.sensitivity),
            )
            staged += 1
        return staged


def _load_connector_config_entries(config_path: Path | None) -> list[dict[str, Any]]:
    """Read the ``connectors:`` list from ``config_path`` (if present).

    Returns the raw list of operator entries — each one is a dict with
    at minimum a ``name`` key. Returns ``[]`` when ``config_path`` is
    ``None``, the file does not exist, or no connectors are configured;
    the worker treats every such case as a no-op sync.
    """
    if config_path is None or not config_path.exists():
        return []
    try:
        import yaml

        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:  # pragma: no cover — yaml parse errors are rare and logged
        logger.warning("worker: failed to load connector config from %s — %s", config_path, exc)
        return []
    entries = raw.get("connectors")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)]


def _run_one_connector_batch(
    db: sqlite3.Connection,
    entry: dict[str, Any],
    bronze_root: Path,
) -> tuple[int, int]:
    """Wire one connector entry through the :class:`ConnectorPipeline`.

    Returns ``(items_indexed, items_dead_lettered)``. Raises on
    registry / pipeline construction failures so the caller's per-entry
    try/except logs them and continues to the next connector.

    Topology v2 Wave C: when the ``topology_v2_runtime`` flag is OFF, the
    chunk writer is constructed via
    :func:`kairix.core.connectors.collection_router._legacy_chunk_writer`
    (paying down the F61 baseline — the writer is built inside the
    framework). When ON, a :class:`CollectionRouter` is built per cc_pair
    and used as the pipeline's chunk_writer; routing happens per-item.
    For the Wave C landing, the ON path is wired through the registry
    but the cc_pair lookup falls back to the legacy single-collection
    writer when no cc_pair has been registered for ``entry["name"]``
    (zero-behavioural-change guarantee).
    """
    from kairix.core.connectors import (
        ConnectorPipeline,
        CursorStore,
        DeadLetterStore,
        DefaultSilverProcessor,
        SqliteDocumentsMediaWriter,
        resolve_connector,
    )
    from kairix.core.connectors.collection_router import _legacy_chunk_writer
    from kairix.core.connectors.registry import build_bronze_from_entry, build_extractor_from_entry
    from kairix.core.features import flag

    name = entry["name"]
    # bronze_root is signature-only; streaming bronze writes no files.
    if bronze_root is not None:
        logger.debug("_run_one_connector_batch: bronze_root parameter is unused.")
    connector_factory = resolve_connector(name)
    connector = connector_factory(entry.get("config", {}))
    extractor = build_extractor_from_entry(entry)
    bronze_store = build_bronze_from_entry(entry, db=db)
    chunk_writer = resolve_chunk_writer_for_entry(db, name, flag_on=bool(flag("topology_v2_runtime")))
    pipeline = ConnectorPipeline(
        db=db,
        bronze=bronze_store,
        silver=DefaultSilverProcessor(documents_media_writer=SqliteDocumentsMediaWriter(db)),
        chunk_writer=chunk_writer,
        entity_graph_sink=_SqliteEntityGraphSink(db),
        cursor_store=CursorStore(db),
        dead_letter=DeadLetterStore(db),
    )
    result = pipeline.run_batch(connector, extractor)
    del _legacy_chunk_writer
    return result.processed, result.dead_lettered


def resolve_chunk_writer_for_entry(
    db: sqlite3.Connection,
    name: str,
    *,
    flag_on: bool,
) -> Any:
    """Resolve the chunk-writer for ``name``, gated by ``flag_on``.

    Flag OFF (default): construct an ``_SqliteChunkWriter`` via the
    framework-internal :func:`legacy_chunk_writer` helper. This pays
    down the F61 baseline — worker.py no longer constructs the writer
    directly; the helper inside ``kairix/core/connectors/`` does.

    Flag ON: look up cc_pair_id for ``name`` in
    ``topology_cc_pairs.name``. If found, return a
    :class:`CollectionRouter` adapter for that cc_pair. If not found,
    fall through to the legacy writer — guarantees bit-for-bit
    behaviour parity until operator config registers cc_pairs (Wave D).

    Returns ``Any`` because the union of ``_SqliteChunkWriter`` and
    ``_CollectionRouterChunkWriter`` is satisfied via duck-typing on
    the ``.upsert(chunks) -> int`` ChunkWriter Protocol shape; both
    return types live in private modules.
    """
    from kairix.core.connectors.collection_router import CollectionRouter, legacy_chunk_writer

    if not flag_on:
        return legacy_chunk_writer(db, collection=name)
    cc_pair_id = _lookup_cc_pair_id_by_name(db, name)
    if cc_pair_id is None:
        return legacy_chunk_writer(db, collection=name)
    router = CollectionRouter(db, cc_pair_id)
    if router.mapping_count() == 0:
        # cc_pair exists but no collection_sources mapped — preserve legacy
        # single-collection behaviour. Wave D operator-config validation
        # will block a cc_pair landing without at least one mapping.
        return legacy_chunk_writer(db, collection=name)
    return _CollectionRouterChunkWriter(router=router)


_resolve_chunk_writer_for_entry = resolve_chunk_writer_for_entry


def _lookup_cc_pair_id_by_name(db: sqlite3.Connection, name: str) -> int | None:
    """SELECT topology_cc_pairs.id WHERE name = ?. Returns None on miss.

    Wraps the raw query so the worker doesn't reach into topology_*
    schema directly (the framework owns those tables; this is the
    operator-name → cc_pair-id bridge).
    """
    try:
        row = db.execute("SELECT id FROM topology_cc_pairs WHERE name = ?", (name,)).fetchone()
    except sqlite3.OperationalError:
        # topology_cc_pairs may not exist on a legacy schema (pre Wave A).
        return None
    return None if row is None else int(row[0])


class _CollectionRouterChunkWriter:
    """ChunkWriter Protocol adapter routing every chunk through CollectionRouter.

    The ChunkWriter Protocol exposes ``upsert(chunks) -> int``; the
    router exposes ``write_chunks(item_id, chunks) -> RouteResult``.
    The adapter bridges by extracting ``item_id`` from the first chunk's
    ``source_uri`` (matches the per-item invariant SilverProcessor
    enforces — every chunk in a single ``upsert`` batch shares one
    ``source_uri``).
    """

    def __init__(self, *, router: Any) -> None:
        self._router = router

    def upsert(self, chunks: Sequence[Chunk]) -> int:
        if not chunks:
            return 0
        item_id = chunks[0].source_uri
        result = self._router.write_chunks(item_id, chunks)
        return int(result.n_written)


def _resolve_config_path_default() -> Path | None:
    """Default boundary read for the ``kairix.config.yaml`` path.

    Wrapped in a module-private helper so :class:`ConnectorSyncDeps` can
    bind it via ``default_factory`` (F6: no ``Optional[Callable] = None``
    self-resolving pattern on the Deps class).
    """
    from kairix.core.search.config_loader import resolve_config_path

    return resolve_config_path()


def _open_db_default() -> sqlite3.Connection:
    """Default DB-factory boundary call — wraps :func:`kairix.core.db.open_db`."""
    from kairix.core.db import open_db

    return open_db()


def _bronze_root_default() -> Path:
    """Default bronze-root resolver — ``data_dir() / "bronze"``."""
    return data_dir() / "bronze"


@dataclass
class ConnectorSyncDeps:
    """Injectable dependencies for :func:`run_connector_sync_pipeline`.

    F6-clean: every field has a ``default_factory`` so production callers
    construct ``ConnectorSyncDeps()`` and get the real boundary calls;
    tests construct ``ConnectorSyncDeps(disabled_fn=lambda: True, ...)``
    and pass it as a single argument. Matches :class:`WorkerDeps`'s
    discipline for the sibling worker callables.

    Fields:
      * ``disabled_fn`` — short-circuit predicate; default
        :func:`connector_sync_disabled`.
      * ``config_path_resolver`` — returns the ``kairix.config.yaml``
        path or ``None`` when no config exists; default
        :func:`resolve_config_path` via :func:`_resolve_config_path_default`.
      * ``db_factory`` — opens a fresh SQLite connection; default
        :func:`kairix.core.db.open_db`.
      * ``bronze_root_resolver`` — returns the Bronze blob root; default
        ``data_dir() / "bronze"``.
    """

    disabled_fn: Callable[[], bool] = field(default_factory=lambda: connector_sync_disabled)
    config_path_resolver: Callable[[], Path | None] = field(default_factory=lambda: _resolve_config_path_default)
    db_factory: Callable[[], sqlite3.Connection] = field(default_factory=lambda: _open_db_default)
    bronze_root_resolver: Callable[[], Path] = field(default_factory=lambda: _bronze_root_default)


def run_connector_sync_pipeline(deps: ConnectorSyncDeps | None = None) -> ConnectorSyncResult:
    """Drive one tick across every configured connector.

    Reads the operator's ``kairix.config.yaml`` ``connectors:`` list,
    resolves each plugin via the entry-point registry, composes the
    canonical :class:`~kairix.core.connectors.ConnectorPipeline` against
    the shared SQLite connection, and runs one batch per connector.

    Returns a :class:`ConnectorSyncResult` aggregating ``items_indexed``
    and ``items_dead_lettered`` across every connector. Per-connector
    failures (registry miss, plugin raise, pipeline rollback) are logged
    and the loop continues — a single misconfigured connector does not
    halt sibling sync work.

    Short-circuits to a zero-counter result when ``deps.disabled_fn``
    (default :func:`kairix.paths.connector_sync_disabled`) returns True
    OR when no connectors are configured (the common case on a vault-
    only deploy).

    Per F37 this function MUST NOT import change-detection libraries
    (``watchdog`` / ``msgraph`` / ``notion_client`` / ``dulwich`` /
    ``slack_sdk.rtm``). Imports route through ``kairix.core.connectors``
    (orchestration) and ``kairix.core.db`` (transaction); the actual
    sync libraries land transitively only when a configured connector
    factory loads its own implementation module.

    See docs/architecture/connector-ingestion-architecture.md §6.
    """
    deps = deps if deps is not None else ConnectorSyncDeps()
    if deps.disabled_fn():
        logger.info("worker: connector sync disabled via KAIRIX_CONNECTOR_SYNC_DISABLED")
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    entries = _load_connector_config_entries(deps.config_path_resolver())
    if not entries:
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    bronze_root = deps.bronze_root_resolver()

    from kairix.core.db.schema import create_schema

    db = deps.db_factory()
    try:
        create_schema(db)
        synced = 0
        failed = 0
        dead_letter_added = 0
        for entry in entries:
            try:
                indexed, dead_lettered = _run_one_connector_batch(db, entry, bronze_root)
            except Exception as exc:
                logger.warning("worker: connector %s failed — %s", entry.get("name"), exc)
                continue
            synced += indexed
            failed += dead_lettered
            dead_letter_added += dead_lettered
        return ConnectorSyncResult(synced=synced, failed=failed, dead_letter_added=dead_letter_added)
    finally:
        db.close()


def run_via_connector_pipeline(deps: ConnectorSyncDeps | None = None) -> ConnectorSyncResult:
    """Flag-ON branch — drive every configured connector through the
    canonical :class:`~kairix.core.connectors.ConnectorPipeline`.

    Thin shim over :func:`run_connector_sync_pipeline`. The split
    exists so the OFF and ON branches stay symmetrical in
    :func:`_default_connector_sync` — one named helper each, no inline
    orchestration. Emits a branch-identifier INFO log so operators
    (and BDD scenarios) can see which path the flag selected this tick.

    ``deps`` is the F6-clean injection seam — production callers omit
    it and the default ``ConnectorSyncDeps()`` factory wires the real
    boundary calls; BDD + integration tests pass a tmp_path-rooted
    deps object so the pipeline runs against a sandboxed DB / config.
    """
    logger.info("worker: connector sync routing via obsidian connector pipeline (flag ON)")
    return run_connector_sync_pipeline(deps)


@dataclass(frozen=True)
class ReextractResult:
    """Outcome of :func:`run_reextract_dead_letter`.

    Frozen per F42. Fields:

    * ``recovered`` — items where extract+silver+writer succeeded;
      dead_letter row deleted.
    * ``still_failing`` — items that raised again; dead_letter row
      kept with bumped failure_count.
    * ``skipped_no_bronze`` — items whose bronze_records row was
      absent (typical after the 2026-05-25 orphan-prune recovery
      where some dead_letter rows pre-date the surviving bronze).
    * ``skipped_no_connector`` — items whose source_name isn't
      configured in the current kairix.config.yaml (operator removed
      the connector but old dead_letter rows remain).
    * ``skipped_source_unavailable`` — Phase 5: streaming-mode rows
      where ``connector.fetch(item_id)`` raised (item deleted from
      source, auth failed, source HTTP 5xx, etc.). The dead_letter row
      is kept for operator triage.
    """

    recovered: int
    still_failing: int
    skipped_no_bronze: int
    skipped_no_connector: int
    skipped_source_unavailable: int = 0  # added in Phase 5


def run_reextract_dead_letter(
    *,
    source_name: str,
    db: sqlite3.Connection | None = None,
    bronze_root: Path | None = None,
    config_path: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> ReextractResult:
    """Re-extract every dead-lettered item for ``source_name``.

    Recovers from past extract failures (e.g. missing libraries fixed
    in a later release) without requiring the source to re-emit the
    items. Walks ``DeadLetterStore.list(source_name)`` and for each
    entry:

    1. Looks up the ``bronze_records`` row for ``(source_name, item_id)``.
       Missing → skipped_no_bronze.
    2. Reads the raw bytes via ``bronze.read(ref)``.
    3. Resolves the connector from the live config so
       ``source_link(item_id)`` and ``sensitivity_for(item_id)`` come
       from the same connector instance that wrote bronze originally.
       Missing connector entry → skipped_no_connector.
    4. Runs ``extractor.extract(raw, mime)`` (whatever extractor is
       currently registered — picks up fixes like #322 markitdown
       extras). Failure → still_failing (row stays in dead_letter,
       failure_count incremented).
    5. Runs silver → chunk_writer → entity_graph_sink.
    6. Clears the dead_letter row.
    7. Commits per item (chunked-commit principle from #321).

    Use after a Dockerfile / connector fix lands to recover items that
    dead-lettered under the old behaviour. ``dry_run`` walks the same
    logic but commits nothing — useful for sizing the recovery before
    committing to it. ``limit`` caps the number of items processed
    (None = all).
    """
    from kairix.core.connectors import DeadLetterStore
    from kairix.core.db import open_db
    from kairix.core.db.schema import create_schema

    db_owned = False
    if db is None:
        db = open_db()
        db_owned = True
    try:
        create_schema(db)
        bronze_root_resolved = bronze_root if bronze_root is not None else _bronze_root_default()
        dead_letter = DeadLetterStore(db)

        entry = _load_connector_entry(source_name, config_path)
        if entry is None:
            rows_no_conn = dead_letter.list(source_name)
            return ReextractResult(
                recovered=0,
                still_failing=0,
                skipped_no_bronze=0,
                skipped_no_connector=len(rows_no_conn),
            )

        # Phase 5: re-extract reads bronze per-row, not per-config — old
        # filesystem-shape rows + new streaming-shape rows can coexist in
        # the same dead_letter table. _read_raw_for_reextract dispatches
        # on ref.raw_path. No bronze store is constructed at this level.

        connector, extractor, silver, chunk_writer, entity_graph_sink = _build_reextract_components(
            source_name=source_name,
            entry=entry,
            db=db,
        )
        rows = dead_letter.list(source_name)
        if limit is not None:
            rows = rows[:limit]
        return _reextract_rows(
            rows=rows,
            db=db,
            bronze_root=bronze_root_resolved,
            extractor=extractor,
            silver=silver,
            chunk_writer=chunk_writer,
            entity_graph_sink=entity_graph_sink,
            connector=connector,
            dead_letter=dead_letter,
            dry_run=dry_run,
        )
    finally:
        if db_owned:
            db.close()


def _load_connector_entry(source_name: str, config_path: Path | None) -> dict[str, Any] | None:
    """Load the YAML connector entry matching ``source_name``.

    Returns ``None`` when the config file is absent or the named source
    isn't declared — both shapes map to ``skipped_no_connector`` at the
    caller, so the caller doesn't need to distinguish.
    """
    resolved = config_path if config_path is not None else _resolve_config_path_default()
    if resolved is None:
        return None
    try:
        cfg = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except Exception:  # pragma: no cover — defensive
        return None
    entries = cfg.get("connectors", []) if isinstance(cfg, dict) else []
    return next((e for e in entries if e.get("name") == source_name), None)


def _build_reextract_components(
    *,
    source_name: str,
    entry: dict[str, Any],
    db: sqlite3.Connection,
) -> tuple[Any, Any, Any, Any, Any]:
    """Wire connector + extractor + silver + chunk_writer + entity-graph sink.

    Mirrors ``_run_one_connector_batch``'s resolution shape so re-extract
    sees identical wiring to the original sync.
    """
    from kairix.core.connectors import DefaultSilverProcessor, SqliteDocumentsMediaWriter, resolve_connector
    from kairix.core.connectors.collection_router import legacy_chunk_writer
    from kairix.core.connectors.registry import build_extractor_from_entry

    connector = resolve_connector(source_name)(entry.get("config", {}))
    # Builds either a single extractor or an EscalatingExtractor depending
    # on whether the entry sets ``extractor_chain: [...]`` or ``extractor: <name>``.
    extractor = build_extractor_from_entry(entry)
    # #336 — wire SqliteDocumentsMediaWriter so re-extract writes the
    # documents_media row that the original sync would have. Without
    # this, re-extracted documents flow through but the per-doc row is
    # silently skipped, leaving F40/F70 blind to recovered docs.
    silver = DefaultSilverProcessor(documents_media_writer=SqliteDocumentsMediaWriter(db))
    chunk_writer = legacy_chunk_writer(db, collection=entry.get("collection", "default"))
    entity_graph_sink = _SqliteEntityGraphSink(db)
    return connector, extractor, silver, chunk_writer, entity_graph_sink


# Re-extract per-row outcome bucket names. Extracted as module-level
# constants so the duplicate-string check (F17) doesn't fire on the
# multiple call/return sites and so the dispatch in _reextract_rows
# becomes a string-equality switch over named buckets rather than
# magic-string comparisons.
_BUCKET_RECOVERED = "recovered"
_BUCKET_STILL_FAILING = "still_failing"
_BUCKET_SKIPPED_NO_BRONZE = "skipped_no_bronze"
_BUCKET_SKIPPED_SOURCE_UNAVAILABLE = "skipped_source_unavailable"
_BUCKET_OK = "ok"


def _reextract_one(
    *,
    entry: Any,
    db: sqlite3.Connection,
    bronze_root: Path,
    extractor: Any,
    silver: Any,
    chunk_writer: Any,
    entity_graph_sink: Any,
    connector: Any,
    dead_letter: Any,
    dry_run: bool,
) -> str:
    """Re-extract one dead-letter row; return a bucket name for the counter.

    Buckets: 'recovered', 'still_failing', 'skipped_no_bronze',
    'skipped_source_unavailable'. Extracted from the inner loop of
    ``_reextract_rows`` to keep that function under the F16 cognitive-
    complexity threshold.
    """
    from kairix.core.protocols import BronzeRef

    row = db.execute(
        "SELECT raw_path, mime, fetched_at FROM bronze_records WHERE source_name = ? AND item_id = ?",
        (entry.source_name, entry.item_id),
    ).fetchone()
    if row is None:
        return _BUCKET_SKIPPED_NO_BRONZE
    db_raw_path = str(row[0])
    ref = BronzeRef(
        source_name=entry.source_name,
        item_id=entry.item_id,
        raw_path=db_raw_path if db_raw_path else None,
        mime=str(row[1]),
        fetched_at=str(row[2]),
    )
    raw_or_none, mime_or_none, outcome = _read_raw_for_reextract(
        ref=ref,
        connector=connector,
        db=db,
        bronze_root=bronze_root,
        item_id=entry.item_id,
    )
    if outcome != _BUCKET_OK:
        return outcome
    assert raw_or_none is not None and mime_or_none is not None
    try:
        doc = extractor.extract(raw_or_none, mime_or_none)
        silver_out = silver.process(
            ref,
            doc,
            source_uri=connector.source_link(entry.item_id),
            source_modified_at=str(row[2]),
            sensitivity=connector.sensitivity_for(entry.item_id),
        )
        chunk_writer.upsert(silver_out.chunks)
        entity_graph_sink.buffer(silver_out.entity_signals)
        dead_letter.clear(entry.source_name, entry.item_id)
        if not dry_run:
            db.commit()
        else:
            db.rollback()
        return _BUCKET_RECOVERED
    except Exception as exc:
        _record_reextract_failure(db=db, dead_letter=dead_letter, entry=entry, exc=exc, dry_run=dry_run)
        return _BUCKET_STILL_FAILING


def _record_reextract_failure(
    *,
    db: sqlite3.Connection,
    dead_letter: Any,
    entry: Any,
    exc: Exception,
    dry_run: bool,
) -> None:
    """Roll back the failed per-item txn and refresh dead-letter bookkeeping.

    GH #351 — bumps ``failure_count`` + sets ``last_attempt = now()`` +
    writes ``last_error`` so operators see fresh state after each
    reextract attempt. Without this, the row reads "3-day-old error"
    even after a brand-new reextract with a fixed extractor (#337
    SharePoint triage hit this).

    ``dry_run`` preserves the "commits nothing" contract — the row is
    NOT touched in that mode, so operators can size a recovery without
    dirtying the table.

    Extracted from :func:`_reextract_one`'s except branch to keep that
    function under the F16 cognitive-complexity threshold.
    """
    db.rollback()
    if dry_run:
        return
    try:
        dead_letter.record(entry.source_name, entry.item_id, f"reextract: {exc}")
        db.commit()
    except Exception:
        # Don't let a bookkeeping failure mask the original failure bucket;
        # operator still gets still_failing surfaced via the counter.
        db.rollback()


def _read_raw_for_reextract(
    *,
    ref: Any,
    connector: Any,
    db: sqlite3.Connection,
    bronze_root: Path,
    item_id: str,
) -> tuple[bytes | None, str | None, str]:
    """Dual-mode read for the Phase 5 re-extract loop.

    Returns ``(raw, mime, outcome)`` where outcome is one of:
    - ``"ok"`` — raw + mime are populated; caller proceeds with extract
    - ``"skipped_source_unavailable"`` — streaming-row connector.fetch raised
    - ``"still_failing"`` — filesystem-row bronze.read raised

    Extracted to keep ``_reextract_rows`` under the F16 cognitive-complexity
    threshold. The branching logic + rollback handling lives here; the
    outer loop only handles the counter bookkeeping.
    """
    if ref.raw_path is None:
        try:
            raw_artefact = connector.fetch(item_id)
            return raw_artefact.raw, raw_artefact.mime, _BUCKET_OK
        except Exception:
            db.rollback()
            return None, None, _BUCKET_SKIPPED_SOURCE_UNAVAILABLE
    try:
        raw, mime = _read_filesystem_bronze(db, bronze_root, ref)
        return raw, mime, _BUCKET_OK
    except Exception:
        db.rollback()
        return None, None, _BUCKET_STILL_FAILING


def _read_filesystem_bronze(_db: sqlite3.Connection, bronze_root: Path, ref: Any) -> tuple[bytes, str]:
    """Read a legacy on-disk bronze blob (pre-Phase-7 filesystem-mode rows).

    Phase 7 removed FilesystemBronzeStore as a writeable class, but old
    dead-letter rows on existing deploys still point at on-disk blobs.
    This helper reads them via raw filesystem I/O so Bug D re-extract
    can still recover those items. Operators who've never run a
    pre-Phase-7 build never hit this branch.
    """
    abs_path = bronze_root / ref.raw_path
    return abs_path.read_bytes(), ref.mime


def _reextract_rows(
    *,
    rows: tuple[Any, ...],
    db: sqlite3.Connection,
    bronze_root: Path,
    extractor: Any,
    silver: Any,
    chunk_writer: Any,
    entity_graph_sink: Any,
    connector: Any,
    dead_letter: Any,
    dry_run: bool,
) -> ReextractResult:
    """Inner loop of :func:`run_reextract_dead_letter` — split out so the
    outer function's setup stays under F16 cognitive complexity."""

    recovered = 0
    still_failing = 0
    skipped_no_bronze = 0
    skipped_source_unavailable = 0

    for entry in rows:
        bucket = _reextract_one(
            entry=entry,
            db=db,
            bronze_root=bronze_root,
            extractor=extractor,
            silver=silver,
            chunk_writer=chunk_writer,
            entity_graph_sink=entity_graph_sink,
            connector=connector,
            dead_letter=dead_letter,
            dry_run=dry_run,
        )
        if bucket == _BUCKET_RECOVERED:
            recovered += 1
        elif bucket == _BUCKET_STILL_FAILING:
            still_failing += 1
        elif bucket == _BUCKET_SKIPPED_NO_BRONZE:
            skipped_no_bronze += 1
        elif bucket == _BUCKET_SKIPPED_SOURCE_UNAVAILABLE:
            skipped_source_unavailable += 1
    return ReextractResult(
        recovered=recovered,
        still_failing=still_failing,
        skipped_no_bronze=skipped_no_bronze,
        skipped_no_connector=0,
        skipped_source_unavailable=skipped_source_unavailable,
    )


@dataclass
class LegacyScannerDeps:
    """Injectable dependencies for :func:`run_via_legacy_document_scanner`.

    F6-clean: every field carries a ``default_factory`` so production
    callers construct ``LegacyScannerDeps()`` and get the real boundary
    functions; tests build ``LegacyScannerDeps(document_root_resolver=...,
    db_factory=...)`` to sandbox the scanner against a tmp_path-rooted
    document store. Mirrors :class:`ConnectorSyncDeps`'s discipline for
    the sibling ON-branch path.

    Fields:
      * ``document_root_resolver`` — returns the legacy document root;
        default :func:`kairix.paths.document_root`.
      * ``db_factory`` — opens the SQLite connection the scanner writes
        through; default :func:`kairix.core.db.open_db`.
    """

    document_root_resolver: Callable[[], Path] = field(default_factory=lambda: document_root)
    db_factory: Callable[[], sqlite3.Connection] = field(default_factory=lambda: _open_db_default)


def run_via_legacy_document_scanner(deps: LegacyScannerDeps | None = None) -> ConnectorSyncResult:
    """Flag-OFF branch — pre-IM-3 ``DocumentScanner`` indexing path.

    Runs the legacy ``kairix.core.db.scanner.DocumentScanner`` over the
    configured :func:`document_root`, then reports the scan counters in
    a :class:`ConnectorSyncResult` so the maintenance-cycle dispatch
    surface stays uniform across branches:

    * ``synced`` = ``ScanReport.new + ScanReport.updated`` — items the
      scanner brought into the index this tick.
    * ``failed`` = ``ScanReport.errors`` — per-file read / hash errors
      the scanner logged but absorbed.
    * ``dead_letter_added`` = 0 — the legacy scanner has no dead-letter
      surface (that's a connector-framework affordance).

    When the document root does not exist the scanner short-circuits to
    a zero-counter result without raising — same no-op posture as the
    ``run_connector_sync_pipeline`` empty-config branch, so flipping the
    flag on/off on an empty deploy is symmetrical.

    ``deps`` is the F6-clean injection seam — production callers omit
    ``deps`` and the default factory wires real boundary calls; BDD +
    integration tests pass a ``LegacyScannerDeps`` rooted at tmp_path so
    the legacy branch never touches the dev's real vault.
    """
    logger.info("worker: connector sync routing via legacy document scanner (flag OFF)")
    deps = deps if deps is not None else LegacyScannerDeps()
    droot = deps.document_root_resolver()
    if not droot.exists():
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    from kairix.core.db.scanner import CollectionConfig, DocumentScanner

    db = deps.db_factory()
    try:
        scanner = DocumentScanner(db, document_root=droot)
        report = scanner.scan([CollectionConfig(name="default", path=".")])
    finally:
        db.close()

    return ConnectorSyncResult(
        synced=report.new + report.updated,
        failed=report.errors,
        dead_letter_added=0,
    )


def _default_neo4j_drain() -> Any:
    """Worker-loop dispatch slot for the Neo4j entity-graph drain (GH #334).

    Wraps the production drain with the live SQLite DB + live Neo4j
    client; returns a
    :class:`kairix.core.curator.drain.NeoDrainResult` so the worker can
    log structured outcomes. The lazy imports keep startup fast — only
    the drain tick pays the cost of loading the graph layer.

    Failure modes:
      * Neo4j unreachable → :func:`run_neo4j_drain_tick` returns
        ``NeoDrainResult(neo4j_available=False, pushed=0)`` and the
        worker logs a single warning. The next tick retries.
      * SQLite read fails → propagates up; the worker's
        ``(Exception, SystemExit)`` discipline at the dispatch site
        keeps the loop alive.

    Tests inject a substitute via ``WorkerDeps(neo4j_drain_fn=fake)``;
    production omits and gets this default. The component-build chain
    (graph client → repo → SQLite handle → tick) lives in
    :func:`kairix.core.curator.drain.run_default_drain_tick` so the
    drain module is self-contained and worker.py stays the thin
    dispatcher. Both modules independently satisfy their per-file
    coverage floors.
    """
    from kairix.core.curator.drain import run_default_drain_tick

    return run_default_drain_tick()


def _default_connector_sync() -> ConnectorSyncResult:
    """Worker-loop dispatch slot for the connector-sync maintenance task.

    Branches on the ``obsidian_connector_primary`` feature flag (PR-6
    of the feature-flag plan; see
    ``docs/architecture/feature-flag-architecture.md`` §7):

    * Flag **OFF** (default) — legacy ``DocumentScanner`` path. Keeps
      pre-IM-3 indexing behaviour intact so the cutover is reversible
      until the flag is retired (see §4 lifecycle).
    * Flag **ON** — :class:`~kairix.core.connectors.ConnectorPipeline`
      path. Each configured connector is driven through
      list_changes → fetch → bronze → silver → cursor.advance.

    Both code paths stay present until the flag retires; F54 requires
    both branches to carry tests (BDD + integration + E2E).

    Production callers reach this via ``WorkerDeps.connector_sync_fn``
    default-factory. Tests that need to pin the flag value compose
    :func:`dispatch_connector_sync` with a ``FakeFeatureFlagResolver``
    and pass the result through ``WorkerDeps(connector_sync_fn=...)``
    — see :func:`dispatch_connector_sync` for the composition shape.
    """
    return dispatch_connector_sync()


def _default_flag_value(name: str) -> bool:
    """Production default for :func:`dispatch_connector_sync`'s
    ``read_flag`` argument — delegates to :func:`kairix.core.features.flag`.

    Lifted to a module-level helper so the dispatcher's signature can
    carry a real callable default (F6-clean) without a per-call
    ``Optional[...] = None`` shape.
    """
    from kairix.core.features import flag as _prod_flag

    return _prod_flag(name)


def dispatch_connector_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_connector_pipeline,
    off_branch: Callable[[], ConnectorSyncResult] = run_via_legacy_document_scanner,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the connector-sync slot.

    Public function that the BDD + integration tests reach to pin a
    specific flag value through a :class:`FakeFeatureFlagResolver`
    without monkey-patching the resolver module (F1-clean). The
    parameter names deliberately avoid the F6 ``_fn`` / ``_resolver``
    / ``_factory`` / ``_loader`` / ``_builder`` / ``_provider``
    suffixes because they're real boundary callables on a public
    composition root, not test-only seams.

    ``on_branch`` / ``off_branch`` default to the real production
    branch helpers — the BDD step file leaves them unchanged and
    observes the branch via the distinct INFO logs each helper emits.
    Integration tests likewise leave them defaulted or pass
    tmp_path-rooted variants when they need to assert against the
    resulting ConnectorSyncResult counters.
    """
    if read_flag("obsidian_connector_primary"):
        return on_branch()
    return off_branch()


def m365_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_m365_email_headers_sync` —
    return a zero-counter result and log the operator-visible signal
    that the M365 connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: m365_email_headers connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_m365_email_headers_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_m365_email_headers_sync` —
    delegates to the canonical :func:`run_connector_sync_pipeline`
    which resolves the ``m365_email_headers`` plugin via its
    entry-point factory and drives the standard ConnectorPipeline.

    The branch log distinguishes the M365 path from the sibling
    obsidian path so operators can tell which connector ran by
    grep-ing INFO logs.
    """
    logger.info("worker: m365_email_headers connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_m365_email_headers_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_m365_email_headers_connector,
    off_branch: Callable[[], ConnectorSyncResult] = m365_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the M365 connector slot.

    Reads the ``connector_m365_email_headers`` flag and routes to the
    ON branch (the standard connector pipeline, which resolves the
    ``m365_email_headers`` plugin) or the OFF branch (a no-op that
    skips the connector entirely). Mirrors
    :func:`dispatch_connector_sync` shape — the BDD + integration
    tests pin the flag through :class:`FakeFeatureFlagResolver` and
    observe the branch via the per-helper INFO log.

    Per KP-2, gating happens at the connector-selection boundary —
    when OFF, the m365 plugin never runs even if listed in
    ``kairix.config.yaml``. When ON, the connector is selected via
    the standard config + entry-point shape.
    """
    if read_flag("connector_m365_email_headers"):
        return on_branch()
    return off_branch()


def sharepoint_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_sharepoint_sync` —
    return zero counters and emit the operator-visible signal that the
    SharePoint connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: sharepoint connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_sharepoint_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_sharepoint_sync` — delegate
    to the canonical :func:`run_connector_sync_pipeline` which resolves
    the ``sharepoint`` plugin via its entry-point factory and drives the
    standard ConnectorPipeline.

    The branch log distinguishes the SharePoint path from the sibling
    M365 / obsidian paths so operators can tell which connector ran by
    grep-ing INFO logs.
    """
    logger.info("worker: sharepoint connector running (flag ON)")
    return run_connector_sync_pipeline()


def github_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_github_sync` — return zero
    counters and emit the operator-visible signal that the GitHub
    connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: github connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_github_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_github_sync` — delegate to
    the canonical :func:`run_connector_sync_pipeline` which resolves
    the ``github`` plugin via its entry-point factory and drives the
    standard ConnectorPipeline.

    The branch log distinguishes the GitHub path from the sibling
    sharepoint / m365_* / dex_crm / obsidian paths so operators can
    tell which connector ran by grep-ing INFO logs.
    """
    logger.info("worker: github connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_github_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_github_connector,
    off_branch: Callable[[], ConnectorSyncResult] = github_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the GitHub connector slot.

    Reads the ``connector_github`` flag and routes to the ON branch
    (the standard connector pipeline, which resolves the ``github``
    plugin) or the OFF branch (a no-op that skips the connector
    entirely). Mirrors :func:`dispatch_sharepoint_sync` shape — the BDD
    + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF, the
    github plugin never runs even if listed in ``kairix.config.yaml``.
    When ON, the connector is selected via the standard config +
    entry-point shape.
    """
    if read_flag("connector_github"):
        return on_branch()
    return off_branch()


def dispatch_sharepoint_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_sharepoint_connector,
    off_branch: Callable[[], ConnectorSyncResult] = sharepoint_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the SharePoint connector slot.

    Reads the ``connector_sharepoint`` flag and routes to the ON branch
    (the standard connector pipeline, which resolves the ``sharepoint``
    plugin) or the OFF branch (a no-op that skips the connector
    entirely). Mirrors :func:`dispatch_connector_sync` shape — the BDD
    + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF, the
    sharepoint plugin never runs even if listed in
    ``kairix.config.yaml``. When ON, the connector is selected via the
    standard config + entry-point shape.
    """
    if read_flag("connector_sharepoint"):
        return on_branch()
    return off_branch()


def notion_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_notion_sync` —
    return zero counters and emit the operator-visible signal that the
    Notion connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: notion connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_notion_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_notion_sync` — delegate
    to the canonical :func:`run_connector_sync_pipeline` which resolves
    the ``notion`` plugin via its entry-point factory and drives the
    standard ConnectorPipeline.

    The branch log distinguishes the Notion path from the sibling
    obsidian / m365 / sharepoint paths so operators can tell which
    connector ran by grep-ing INFO logs.
    """
    logger.info("worker: notion connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_notion_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_notion_connector,
    off_branch: Callable[[], ConnectorSyncResult] = notion_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the Notion connector slot.

    Reads the ``connector_notion`` flag and routes to the ON branch
    (the standard connector pipeline, which resolves the ``notion``
    plugin) or the OFF branch (a no-op that skips the connector
    entirely). Mirrors :func:`dispatch_sharepoint_sync` shape — the
    BDD + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF, the
    notion plugin never runs even if listed in ``kairix.config.yaml``.
    When ON, the connector is selected via the standard config +
    entry-point shape.
    """
    if read_flag("connector_notion"):
        return on_branch()
    return off_branch()


def gmail_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_gmail_sync` —
    return zero counters and emit the operator-visible signal that the
    Gmail connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: gmail connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_gmail_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_gmail_sync` — delegate
    to the canonical :func:`run_connector_sync_pipeline` which resolves
    the ``gmail`` plugin via its entry-point factory and drives the
    standard ConnectorPipeline.

    The branch log distinguishes the Gmail path from the sibling
    obsidian / m365 / sharepoint / notion paths so operators can tell
    which connector ran by grep-ing INFO logs.
    """
    logger.info("worker: gmail connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_gmail_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_gmail_connector,
    off_branch: Callable[[], ConnectorSyncResult] = gmail_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the Gmail connector slot.

    Reads the ``connector_gmail`` flag and routes to the ON branch
    (the standard connector pipeline, which resolves the ``gmail``
    plugin) or the OFF branch (a no-op that skips the connector
    entirely). Mirrors :func:`dispatch_sharepoint_sync` shape — the BDD
    + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF, the
    gmail plugin never runs even if listed in ``kairix.config.yaml``.
    When ON, the connector is selected via the standard config +
    entry-point shape.
    """
    if read_flag("connector_gmail"):
        return on_branch()
    return off_branch()


def google_drive_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_google_drive_sync` —
    return zero counters and emit the operator-visible signal that the
    Google Drive connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: google_drive connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_google_drive_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_google_drive_sync` —
    delegates to the canonical :func:`run_connector_sync_pipeline`
    which resolves the ``google_drive`` plugin via its entry-point
    factory and drives the standard ConnectorPipeline.

    The branch log distinguishes the Google Drive path from the
    sibling sharepoint / notion / m365 paths so operators can tell
    which connector ran by grep-ing INFO logs.
    """
    logger.info("worker: google_drive connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_google_drive_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_google_drive_connector,
    off_branch: Callable[[], ConnectorSyncResult] = google_drive_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the Google Drive connector slot.

    Reads the ``topology_v2_google_drive`` flag and routes to the ON
    branch (the standard connector pipeline, which resolves the
    ``google_drive`` plugin) or the OFF branch (a no-op that skips the
    connector entirely). Mirrors :func:`dispatch_sharepoint_sync` shape —
    the BDD + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF, the
    google_drive plugin never runs even if listed in
    ``kairix.config.yaml``. When ON, the connector is selected via the
    standard config + entry-point shape.
    """
    if read_flag("topology_v2_google_drive"):
        return on_branch()
    return off_branch()


def apple_caldav_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_apple_caldav_sync` —
    return zero counters and emit the operator-visible signal that the
    Apple CalDAV connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: apple_caldav connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_apple_caldav_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_apple_caldav_sync` —
    delegate to the canonical :func:`run_connector_sync_pipeline` which
    resolves the ``apple_caldav`` plugin via its entry-point factory
    and drives the standard ConnectorPipeline.

    The branch log distinguishes the Apple CalDAV path from the
    sibling m365_calendar / sharepoint / notion paths so operators
    can tell which connector ran by grep-ing INFO logs.
    """
    logger.info("worker: apple_caldav connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_apple_caldav_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_apple_caldav_connector,
    off_branch: Callable[[], ConnectorSyncResult] = apple_caldav_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the Apple CalDAV connector slot.

    Reads the ``topology_v2_apple_caldav`` flag and routes to the ON
    branch (the standard connector pipeline, which resolves the
    ``apple_caldav`` plugin) or the OFF branch (a no-op that skips
    the connector entirely). Mirrors :func:`dispatch_notion_sync`
    shape — the BDD + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF,
    the apple_caldav plugin never runs even if listed in
    ``kairix.config.yaml``. When ON, the connector is selected via
    the standard config + entry-point shape.

    Unlike the M365 connector flags (which have a separate
    ``connector_*`` introduce flag distinct from the topology-v2
    cutover flag), the Apple CalDAV connector ships behind a single
    capability flag — the connector is brand new at landing time so
    there's no legacy single-cursor shape to preserve; the
    ``topology_v2_apple_caldav`` flag gates the entire plugin until
    it soaks against a production iCloud account.
    """
    if read_flag("topology_v2_apple_caldav"):
        return on_branch()
    return off_branch()


def google_calendar_off_branch_noop() -> ConnectorSyncResult:
    """OFF-branch default for :func:`dispatch_google_calendar_sync` —
    return zero counters and emit the operator-visible signal that the
    Google Calendar connector is gated off.

    F6-clean: a real callable default, no ``None``. Public so the
    feature-flag BDD steps can reach it without an internal-name
    import (F5).
    """
    logger.info("worker: google_calendar connector gated off (flag OFF)")
    return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)


def run_via_google_calendar_connector() -> ConnectorSyncResult:
    """ON-branch default for :func:`dispatch_google_calendar_sync` —
    delegate to the canonical :func:`run_connector_sync_pipeline` which
    resolves the ``google_calendar`` plugin via its entry-point factory
    and drives the standard ConnectorPipeline.

    The branch log distinguishes the Google Calendar path from the
    sibling m365 / obsidian / notion paths so operators can tell which
    connector ran by grep-ing INFO logs.
    """
    logger.info("worker: google_calendar connector running (flag ON)")
    return run_connector_sync_pipeline()


def dispatch_google_calendar_sync(
    read_flag: Callable[[str], bool] = _default_flag_value,
    on_branch: Callable[[], ConnectorSyncResult] = run_via_google_calendar_connector,
    off_branch: Callable[[], ConnectorSyncResult] = google_calendar_off_branch_noop,
) -> ConnectorSyncResult:
    """Compose the flag-branching dispatcher for the Google Calendar slot.

    Reads the ``topology_v2_google_calendar`` flag and routes to the ON
    branch (the standard connector pipeline, which resolves the
    ``google_calendar`` plugin) or the OFF branch (a no-op that skips
    the connector entirely). Mirrors :func:`dispatch_m365_email_headers_sync`
    shape — the BDD + integration tests pin the flag through
    :class:`FakeFeatureFlagResolver` and observe the branch via the
    per-helper INFO log.

    Gating happens at the connector-selection boundary — when OFF, the
    google_calendar plugin never runs even if listed in
    ``kairix.config.yaml``. When ON, the connector is selected via the
    standard config + entry-point shape. The flag defaults OFF until
    Google Workspace OAuth credentials are provisioned (tracked GH #356).
    """
    if read_flag("topology_v2_google_calendar"):
        return on_branch()
    return off_branch()


# ---------------------------------------------------------------------------
# KFEAT-021 Phase 1 — maintenance scheduler wiring (behind the
# ``maintenance_loop`` feature flag). When the flag is OFF the
# :func:`maybe_run_maintenance_loop_tick` helper is a structural no-op
# (no DB open, no scheduler instantiated) — bit-for-bit pre-KFEAT-021
# behaviour is preserved.
# ---------------------------------------------------------------------------


@dataclass
class MaintenanceLoopDeps:
    """Injectable dependencies for :func:`run_maintenance_loop_tick`.

    F6-clean: every field has a ``default_factory`` so production
    callers omit the Deps and get the real boundary calls; tests pass
    fakes to drive the OFF / ON / failure branches without monkey-
    patching kairix internals.

    Fields:
      * ``flag_reader`` — returns the effective value of the named
        feature flag. Default :func:`_default_flag_value`. Tests pass
        a lambda returning a deterministic bool to pin the gate.
      * ``db_factory`` — opens the SQLite connection the scheduler
        prunes through; default :func:`_open_db_default`.
      * ``retention_days_resolver`` — returns the retention window in
        days; default :func:`maintenance_retention_days` (reads
        ``KAIRIX_MAINTENANCE_RETENTION_DAYS``).
      * ``scheduler_factory`` — builds a
        :class:`~kairix.core.maintenance.MaintenanceScheduler` for the
        given connection + retention window. Default constructs the
        production scheduler with default Deps; tests pass a factory
        that returns a Fake with pre-canned tick results.
      * ``prune_orphans_per_tick_cap`` — per-tick row cap forwarded to
        :class:`MaintenanceScheduler` so its orphan scan stays bounded
        on production-scale DBs. Operators can tune via the worker
        deps wiring; default 1000 matches the scheduler default and
        keeps one tick under 5s on a 2M-row table.
    """

    flag_reader: Callable[[str], bool] = field(default_factory=lambda: _default_flag_value)
    db_factory: Callable[[], sqlite3.Connection] = field(default_factory=lambda: _open_db_default)
    retention_days_resolver: Callable[[], int] = field(default_factory=lambda: maintenance_retention_days)
    scheduler_factory: Callable[[sqlite3.Connection, int, int], Any] = field(
        default_factory=lambda: _default_scheduler_factory
    )
    prune_orphans_per_tick_cap: int = 1000


def _default_scheduler_factory(db: sqlite3.Connection, retention_days: int, prune_orphans_per_tick_cap: int) -> Any:
    """Production seam — build a :class:`MaintenanceScheduler` with prod Deps.

    Lazy import keeps the worker importable on hosts that haven't yet
    landed the maintenance module (defensive — Phase 1 is forward-only
    but we keep the boundary tidy).
    """
    from kairix.core.maintenance import MaintenanceScheduler

    return MaintenanceScheduler(
        db,
        retention_days=retention_days,
        prune_orphans_per_tick_cap=prune_orphans_per_tick_cap,
    )


def run_maintenance_loop_tick(deps: MaintenanceLoopDeps | None = None) -> Any:
    """Run one ``MaintenanceScheduler.tick`` (flag-gated).

    Returns the :class:`MaintenanceTickResult` envelope when the flag
    is ON, or ``None`` when the flag is OFF (structural no-op). The
    structured ``maintenance_tick_completed`` log line carries the
    same envelope fields so log-only consumers see the cadence
    without parsing the return value.

    Per the KFEAT-021 brief: when the flag is OFF this MUST be a
    bit-for-bit no-op so flipping the flag in / out is reversible.

    ``deps`` is the F6-clean injection seam — production callers omit
    it; the BDD + integration tests pass a :class:`MaintenanceLoopDeps`
    with the flag pinned through a :class:`FakeFeatureFlagResolver` so
    each branch is exercised against the real scheduler.
    """
    deps = deps if deps is not None else MaintenanceLoopDeps()
    if not deps.flag_reader("maintenance_loop"):
        # Flag OFF — log nothing (avoid spamming on every loop iter)
        # and return None so the worker treats the tick as skipped.
        return None

    retention = deps.retention_days_resolver()
    db = deps.db_factory()
    try:
        from kairix.core.db.schema import create_schema

        create_schema(db)
        scheduler = deps.scheduler_factory(db, retention, deps.prune_orphans_per_tick_cap)
        result = scheduler.tick(db)
    except Exception as exc:
        logger.warning("worker: maintenance tick raised — %s", exc)
        return None
    finally:
        db.close()
    return result


def maybe_run_maintenance_loop_tick(
    *,
    deps: MaintenanceLoopDeps | None,
    transition: Callable[[WorkerPhase], None],
    state: WorkerState,
    state_path: Path,
    write_state_fn: Callable[[WorkerState, Path], None],
    now: float,
    last_tick_at: float,
    interval_seconds: int,
) -> float:
    """Run a maintenance tick when due; persist state; return the new ``last_tick_at``.

    When the flag is OFF, this is a structural no-op — the scheduler
    is never instantiated and the DB is never opened (the flag check
    inside :func:`run_maintenance_loop_tick` short-circuits). The
    ``last_tick_at`` value flows back unchanged so the worker loop's
    next-due calculation is unaffected.

    Cadence: a tick is due when ``now - last_tick_at >= interval`` OR
    when ``last_tick_at == 0`` (first cycle post-flag-flip / restart).
    The flag check is the OUTER gate; the cadence is the inner gate.
    """
    from kairix.core.maintenance import is_tick_due

    if not is_tick_due(now, last_tick_at, interval_seconds):
        return last_tick_at

    transition(WorkerPhase.MAINTENANCE)
    result = run_maintenance_loop_tick(deps)
    transition(WorkerPhase.IDLE)
    if result is None:
        # Flag OFF — no tick fired. Don't advance the timestamp so the
        # next loop iter re-checks (the OFF→ON flip should fire
        # immediately rather than wait an interval).
        return last_tick_at

    state.last_maintenance_tick_at = now
    state.last_maintenance_orphans_pruned = int(getattr(result, "orphans_pruned", 0))
    state.last_maintenance_pruned_table_size = int(getattr(result, "pruned_table_size", 0))
    state.last_maintenance_elapsed_ms = int(getattr(result, "elapsed_ms", 0))
    write_state_fn(state, state_path)
    return now


@dataclass
class WorkerDeps:
    """Injectable dependencies for the worker loop and its task helpers.

    Replaces the F6-violating ``embed_fn=None`` / ``entity_seed_fn=None`` /
    ``health_check_fn=None`` / ``wikilinks_fn=None`` / ``sleep_fn=None``
    test-only kwargs with a typed dataclass. Production code calls
    ``main()`` without ``deps`` and the default factory wires the real
    task callables. Tests construct
    ``WorkerDeps(embed=fake, sleep=lambda _s: None)`` and pass it through.

    Each callable field is non-Optional with a ``default_factory`` (per
    CLAUDE.md F6 guidance: avoid the ``Optional[Callable] + post-init``
    pattern that "just landed a mypy bug") so mypy sees the production
    callable directly — no ``assert deps.x is not None`` ladder is
    needed inside the worker loop.
    """

    embed: Callable[[], Any] = field(default_factory=lambda: _default_embed)
    entity_seed: Callable[[], None] = field(default_factory=lambda: _default_entity_seed)
    health_check: Callable[[], list[Any]] = field(default_factory=lambda: _default_health_check)
    wikilinks: Callable[[], None] = field(default_factory=lambda: _default_wikilinks_inject)
    # SC-6 — connector-framework seam (Wave 1 wires; Wave 2 implements).
    # Same F6-clean default_factory pattern as the four task callables
    # above. Tests pass a Fake; production omits and gets the
    # NotImplementedError-raising default until Wave 2 swaps it for the
    # real ``kairix.core.connectors`` dispatcher.
    connector_sync_fn: Callable[[], ConnectorSyncResult] = field(default_factory=lambda: _default_connector_sync)
    # GH #334 — Neo4j entity-graph drain dispatch slot. Same F6-clean
    # default_factory shape as ``connector_sync_fn``. Tests pass a
    # Fake; production omits and gets ``_default_neo4j_drain`` which
    # wires the live SQLite + Neo4j client. The return type is
    # ``NeoDrainResult`` (frozen dataclass) — typed as ``Any`` here so
    # the import stays inside the function body (lazy load of the
    # graph layer keeps worker boot fast).
    neo4j_drain_fn: Callable[[], Any] = field(default_factory=lambda: _default_neo4j_drain)
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)
    # #224 phase 4-5 combined — observable state + pause flag.
    # ``state`` is the in-memory dataclass the loop mutates on phase changes.
    # ``state`` defaults to None so the boot path in main() can read prior
    # state off disk first (restart_count survives container restarts).
    # ``state_path`` is where it gets persisted via ``write_state_fn`` so
    # operators (and ``kairix worker status``) can read it.
    # ``read_state_fn`` is the read-side test seam mirroring ``write_state_fn``.
    # ``pause_flag_path`` is the touch-file the operator-facing
    # ``kairix worker pause/resume`` toggles; the loop polls it each
    # iteration. All are F6-clean (typed, default_factory).
    state: WorkerState = field(default_factory=WorkerState)
    state_path: Path = field(default_factory=worker_state_path)
    write_state_fn: Callable[[WorkerState, Path], None] = field(default_factory=lambda: write_state)
    read_state_fn: Callable[[Path], WorkerState | None] = field(default_factory=lambda: read_state)
    pause_flag_path: Path = field(default_factory=worker_pause_flag_path)
    # KFEAT-021 Phase 1 — maintenance-loop tick deps. F6-clean: a real
    # MaintenanceLoopDeps default; tests pass a substitute with the flag
    # pinned via FakeFeatureFlagResolver so the flag-OFF / flag-ON
    # branches are exercised against the real scheduler.
    maintenance_loop_deps: MaintenanceLoopDeps = field(default_factory=MaintenanceLoopDeps)


@dataclass(frozen=True)
class EmbedRunOutcome:
    """Structured outcome of one embed pass — used by ``main()`` to update
    the persisted ``WorkerState`` counters.

    Field semantics mirror ``EmbedPipelineResult`` but with safe-default
    integers so a legacy stub returning a sparse object still feeds the
    state counters cleanly.
    """

    did_work: bool
    embedded: int = 0
    failed: int = 0
    recall_passed: bool | None = None


def _log_embed_complete(embedded: Any, failed: Any, recall_score: Any) -> None:
    """Emit the standard 'embed complete' info line with recall as percentage or n/a."""
    recall_str = f"{recall_score:.0%}" if isinstance(recall_score, float) else "n/a"
    logger.info("worker: embed complete — embedded=%s failed=%s recall=%s", embedded, failed, recall_str)


def _log_embed_warnings(failed: Any, recall_passed: Any, recall_alert: Any, diagnostics: list[Any]) -> None:
    """Emit failure / recall-gate / diagnostic warnings from an embed result."""
    if isinstance(failed, int) and failed > 0:
        logger.warning("worker: %d chunks failed during embed", failed)
    if recall_passed is False:
        logger.warning(
            "worker: recall gate alert — %s",
            recall_alert or "search quality degraded; see kairix onboard check",
        )
    for diag in diagnostics:
        logger.warning("worker: %s", diag)


def _outcome_from_result(result: Any) -> EmbedRunOutcome:
    """Map an ``EmbedPipelineResult``-shaped object into a typed ``EmbedRunOutcome``."""
    embedded = getattr(result, "embedded", None)
    failed = getattr(result, "failed", None)
    recall_passed = getattr(result, "recall_passed", None)
    diagnostics = getattr(result, "diagnostics", None) or []
    _log_embed_complete(embedded, failed, getattr(result, "recall_score", None))
    _log_embed_warnings(failed, recall_passed, getattr(result, "recall_alert", None), diagnostics)
    did_work = (isinstance(embedded, int) and embedded > 0) or (isinstance(failed, int) and failed > 0)
    return EmbedRunOutcome(
        did_work=did_work,
        embedded=embedded if isinstance(embedded, int) else 0,
        failed=failed if isinstance(failed, int) else 0,
        recall_passed=recall_passed if isinstance(recall_passed, bool) else None,
    )


def run_embed_with_outcome(deps: WorkerDeps | None = None) -> EmbedRunOutcome:
    """Run incremental embed and return a structured outcome.

    Same try/except/logging discipline as ``run_embed`` (see its
    docstring for the "never crash the worker" rationale); this variant
    additionally surfaces the counters main() folds into ``WorkerState``.
    """
    deps = deps if deps is not None else WorkerDeps()
    try:
        logger.info("worker: starting incremental embed")
        result = deps.embed()
        if result is None:
            logger.info("worker: embed complete")
            return EmbedRunOutcome(did_work=False)
        return _outcome_from_result(result)
    except (Exception, SystemExit) as exc:
        logger.warning("worker: embed pipeline raised — %s", exc)
        return EmbedRunOutcome(did_work=False)


def run_embed(deps: WorkerDeps | None = None) -> bool:
    """Run incremental embed — indexes new and changed documents.

    Returns ``True`` when the embed run did real work (embedded > 0 or
    failed > 0), ``False`` when it was a no-op. The main loop uses this
    signal to apply idle-backoff per #224.

    The worker treats every outcome of the embed pipeline as
    non-fatal: failed chunks, recall-gate alerts, and unexpected
    exceptions are all logged and the worker continues to the next
    interval. This decoupling is deliberate — the worker's job is to
    KEEP RUNNING on a schedule; the embed use case's job is to do the
    work and report what happened.

    The embed use case returns an ``EmbedPipelineResult`` dataclass that
    the worker inspects and logs — it must NOT call a code path that uses
    ``sys.exit()`` (e.g. the embed CLI) because ``SystemExit`` is not
    caught by ``except Exception`` and any gate alert would kill the
    worker process.

    ``deps.embed`` is the injection seam: tests pass a callable returning
    either the result dataclass or None (legacy). Production passes
    ``_default_embed`` which runs the use case.
    """
    return run_embed_with_outcome(deps).did_work


def compute_embed_interval(base: int, noop_streak: int) -> int:
    """Apply exponential idle-backoff after a streak of no-op embed runs.

    No backoff until ``EMBED_BACKOFF_NOOP_THRESHOLD`` consecutive no-ops.
    After that, each additional no-op doubles the interval, capped at
    ``EMBED_BACKOFF_MAX_INTERVAL`` (4 hours). The exponent is
    ``noop_streak - threshold + 1`` so the FIRST backoff hop is 2x, not 1x.

    Implements #224's "Add backoff/jitter when scans find no new or
    changed work" acceptance criterion.
    """
    if noop_streak <= EMBED_BACKOFF_NOOP_THRESHOLD:
        return base
    exponent = noop_streak - EMBED_BACKOFF_NOOP_THRESHOLD
    return int(min(base * (2**exponent), EMBED_BACKOFF_MAX_INTERVAL))


def run_entity_seed(deps: WorkerDeps | None = None) -> None:
    """Run entity relationship seeding from document store structure.

    Treats every outcome as non-fatal: the underlying store-crawl CLI
    (``kairix.knowledge.store.cli``) calls ``sys.exit(0)`` on success
    and ``sys.exit(1)`` on error. Catch ``(Exception, SystemExit)`` at
    every CLI boundary so a "successful" ``sys.exit(0)`` from the
    callee can't terminate the worker process. Same discipline as
    ``run_embed`` and ``run_wikilinks_inject``.

    Args:
        deps: Injectable worker dependencies. Tests construct
              ``WorkerDeps(entity_seed=fake)``; production omits the
              kwarg and the default factory wires the real store crawl
              CLI entry point.
    """
    deps = deps if deps is not None else WorkerDeps()
    try:
        logger.info("worker: starting entity seed")
        deps.entity_seed()
        logger.info("worker: entity seed complete")
    except (Exception, SystemExit) as exc:
        logger.warning("worker: entity seed raised — %s", exc)


def run_wikilinks_inject(deps: WorkerDeps | None = None) -> None:
    """Inject ``[[wikilinks]]`` on first mention into agent-written documents.

    Closes #100 — the host cron's nightly ``kairix wikilinks inject
    --changed`` was lost in the Docker migration. The worker now runs
    it on the same cadence as embed (hourly) so new agent-written notes
    get linked to known entities.

    Treats every outcome as non-fatal: the wikilinks CLI may
    ``sys.exit(1)`` when entities aren't loaded yet (pre-first-seed
    bootstrapping), and that must NOT terminate the worker. Same
    ``(Exception, SystemExit)`` discipline as ``run_embed``.

    ``deps.wikilinks`` is the injection seam tests use; production
    falls through to ``_default_wikilinks_inject``.
    """
    deps = deps if deps is not None else WorkerDeps()
    try:
        logger.info("worker: starting wikilinks inject")
        deps.wikilinks()
        logger.info("worker: wikilinks inject complete")
    except (Exception, SystemExit) as exc:
        logger.warning("worker: wikilinks inject raised — %s", exc)


def run_health_check(deps: WorkerDeps | None = None) -> None:
    """Log a health check.

    Treats every outcome as non-fatal — including ``SystemExit`` — for
    the same reason as ``run_entity_seed``: a maintenance helper that
    calls ``sys.exit`` must not terminate the worker process. Catch
    ``(Exception, SystemExit)`` at every CLI boundary.

    Args:
        deps: Injectable worker dependencies. Tests construct
              ``WorkerDeps(health_check=fake)``; production omits the
              kwarg and the default factory wires ``run_all_checks``.
    """
    deps = deps if deps is not None else WorkerDeps()
    try:
        results = deps.health_check()
        passed = sum(1 for r in results if r.ok)
        total = len(results)
        logger.info("worker: health check %d/%d passed", passed, total)
    except (Exception, SystemExit) as exc:
        logger.warning("worker: health check raised — %s", exc)


def run_connector_sync(deps: WorkerDeps | None = None) -> None:
    """Drive one connector-framework sync tick (SC-6 seam).

    Wave 1 wires this as a no-op-friendly dispatch slot: the default
    ``deps.connector_sync_fn`` raises ``NotImplementedError`` and we
    catch it here so a pre-Wave-2 deploy does not crash the worker.
    Wave 2 plugs in the real ``kairix.core.connectors`` dispatcher and
    the same call path becomes the production sync surface (per F37
    this function MUST NOT import change-detection libraries directly —
    those live under ``kairix/connectors/<name>/`` and are reached via
    ``kairix/core/connectors/``).

    Treats every other outcome as non-fatal — same ``(Exception, SystemExit)``
    discipline as the other ``run_*`` helpers. A failing connector
    must not bring the worker process down; failures are logged and
    surfaced via the structured ``ConnectorSyncResult`` on the next
    successful tick.

    Args:
        deps: Injectable worker dependencies. Tests construct
              ``WorkerDeps(connector_sync_fn=fake)``; production omits
              the kwarg and the default factory wires
              ``_default_connector_sync`` (Wave-2-implemented).
    """
    deps = deps if deps is not None else WorkerDeps()
    try:
        logger.info("worker: starting connector sync")
        result = deps.connector_sync_fn()
        logger.info(
            "worker: connector sync complete — synced=%d failed=%d dead_letter_added=%d",
            result.synced,
            result.failed,
            result.dead_letter_added,
        )
    except NotImplementedError:
        # Wave 1: the default raises this. The slot is wired but the
        # body is not yet implemented. Log once-per-tick so operators
        # can see the worker reached the dispatch slot without it
        # crashing the loop. Wave 2 removes the default raise and this
        # branch becomes dead-but-harmless.
        logger.warning("worker: connector sync not yet implemented (Wave 2)")
    except (Exception, SystemExit) as exc:
        logger.warning("worker: connector sync raised — %s", exc)


def run_neo4j_drain(deps: WorkerDeps | None = None) -> None:
    """GH #334 — drive one Neo4j entity-graph drain tick.

    Invokes ``deps.neo4j_drain_fn`` (default
    :func:`_default_neo4j_drain`) and logs the structured
    :class:`~kairix.core.curator.drain.NeoDrainResult`. Mirrors the
    ``(Exception, SystemExit)`` discipline of every other worker
    maintenance helper — failures inside the drain must not bring the
    worker process down. A graph outage shows up as
    ``neo4j_available=false`` in the result envelope and the next tick
    retries.

    Tests construct ``WorkerDeps(neo4j_drain_fn=fake)``; production
    omits the kwarg and the default factory wires
    :func:`_default_neo4j_drain`.
    """
    deps = deps if deps is not None else WorkerDeps()
    try:
        logger.info("worker: starting neo4j drain")
        result = deps.neo4j_drain_fn()
        if not getattr(result, "neo4j_available", True):
            logger.warning("worker: neo4j drain skipped — backend unavailable; will retry next tick")
            return
        logger.info(
            "worker: neo4j drain complete — pushed=%d failed=%d skipped_relationships=%d elapsed_ms=%d",
            getattr(result, "pushed", 0),
            getattr(result, "failed", 0),
            getattr(result, "skipped_relationships", 0),
            getattr(result, "elapsed_ms", 0),
        )
    except (Exception, SystemExit) as exc:
        logger.warning("worker: neo4j drain raised — %s", exc)


@dataclass
class _Schedule:
    """Worker task interval config — bundles the cadence ints.

    Scalar config (not a test-injection seam); main() builds this once
    from kwargs + module defaults so the inner loop helpers can pass a
    single value around rather than discrete ``_embed_interval`` ints.
    SC-6 added ``connector_sync`` alongside the four maintenance cadences;
    GH #334 added ``neo4j_drain`` for the Curator-coupling boundary.
    """

    embed: int
    entity: int
    health: int
    wikilinks: int
    connector_sync: int
    neo4j_drain: int


def _resolve_schedule(
    embed_interval: int | None,
    entity_seed_interval: int | None,
    health_check_interval: int | None,
    wikilinks_interval: int | None,
    connector_sync_interval: int | None,
    neo4j_drain_interval: int | None = None,
) -> _Schedule:
    """Fold kwargs + module defaults into a single ``_Schedule``."""
    return _Schedule(
        embed=embed_interval if embed_interval is not None else EMBED_INTERVAL,
        entity=entity_seed_interval if entity_seed_interval is not None else ENTITY_SEED_INTERVAL,
        health=health_check_interval if health_check_interval is not None else HEALTH_CHECK_INTERVAL,
        wikilinks=wikilinks_interval if wikilinks_interval is not None else WIKILINKS_INTERVAL,
        connector_sync=connector_sync_interval if connector_sync_interval is not None else CONNECTOR_SYNC_INTERVAL,
        neo4j_drain=neo4j_drain_interval if neo4j_drain_interval is not None else NEO4J_DRAIN_INTERVAL,
    )


@dataclass
class PreflightDeps:
    """Injectable dependencies for :func:`_run_preflight_at_boot`.

    F6-clean: each field carries a ``default_factory`` so production
    callers construct ``PreflightDeps()`` and get the real boundary
    functions; tests pass a ``PreflightDeps(db_factory=fake,
    strict_fn=lambda: True)`` rooted at tmp_path to drive the boot
    audit against a sandboxed DB. Mirrors the discipline established
    by :class:`WorkerDeps` / :class:`ConnectorSyncDeps`.

    Fields:
      * ``db_factory`` — opens the SQLite connection preflight should
        audit; default :func:`kairix.core.db.open_db`.
      * ``strict_fn`` — returns True when boot should abort on any
        error-severity gap; default :func:`kairix.paths.preflight_strict`
        (reads ``KAIRIX_PREFLIGHT_STRICT``).
    """

    db_factory: Callable[[], sqlite3.Connection] = field(default_factory=lambda: _open_db_default)
    strict_fn: Callable[[], bool] = field(default_factory=lambda: preflight_strict)


def _run_preflight_at_boot(deps: PreflightDeps | None = None) -> bool:
    """Run the persistence-integrity audit before the first embed cycle.

    Returns True iff boot should continue. Logs the report at INFO when
    healthy, WARNING-per-gap when not. In strict mode
    (``KAIRIX_PREFLIGHT_STRICT=1``) returns False on any error-severity
    gap so the worker exits non-zero; otherwise always returns True so
    slightly-degraded boots surface as warnings instead of crashlooping.

    ``deps`` is the F6-clean injection seam: production callers omit
    ``deps`` and the default factory wires real boundary calls; tests
    pass a :class:`PreflightDeps` rooted at tmp_path so the boot path
    never touches the dev's real index.
    """
    from kairix.core.db.integrity import check_integrity

    deps = deps if deps is not None else PreflightDeps()

    try:
        db = deps.db_factory()
    except Exception as exc:  # pragma: no cover - boundary
        logger.warning("worker: preflight could not open db — %s", exc)
        return True

    try:
        report = check_integrity(db)
    except Exception as exc:
        logger.warning("worker: preflight integrity check raised — %s", exc)
        return True
    finally:
        db.close()

    if report.healthy and not report.gaps:
        logger.info("worker: preflight integrity check passed")
        return True

    error_gaps = [g for g in report.gaps if g.severity == "error"]
    logger.warning(
        "worker: preflight integrity check found %d gap(s) — %d error / %d warn / %d info",
        len(report.gaps),
        sum(1 for g in report.gaps if g.severity == "error"),
        sum(1 for g in report.gaps if g.severity == "warn"),
        sum(1 for g in report.gaps if g.severity == "info"),
    )
    for gap in report.gaps:
        remediation_first_line = gap.remediation.split(";")[0].strip()
        logger.warning(
            "worker: preflight gap — [%s] %s count=%d — %s",
            gap.severity,
            gap.invariant,
            gap.count,
            remediation_first_line,
        )

    if error_gaps and deps.strict_fn():
        logger.warning(
            "worker: preflight strict mode active — %d error-severity gap(s) — exiting",
            len(error_gaps),
        )
        return False
    return True


def probe_vec_index_at_boot(
    *,
    db_path: Path | None = None,
    enabled: bool | None = None,
) -> None:
    """Open the vec_index at worker boot to surface recovery actions early.

    The 2026-05-31 production bug had the operator discover index
    corruption ~6 hours into a force-embed run, via the recall canary
    check at the end. This probe runs the same load_or_recreate() at
    boot so any recovery (orphan .tmp promotion, corrupt-file
    recreation) is logged immediately AND fixed in place before the
    first embed tick. The probe is idempotent — opening a healthy
    index is a no-op.

    Disabled when ``enabled=False`` (or, when ``enabled`` is None,
    when ``worker_writes_vec_index()`` returns False — the operator
    opted out of worker-side vec writes; the probe wouldn't help and
    the open shouldn't happen).

    Never raises — failures log as WARNING and boot continues. The
    embed pipeline's own load_or_recreate() will retry at first-tick
    if this probe somehow missed a state transition.

    ``db_path`` / ``enabled`` are the F2-clean test seams; production
    callers omit both and the boundary reads from KairixPaths.
    """
    try:
        if enabled is None:
            from kairix.paths import worker_writes_vec_index

            enabled = worker_writes_vec_index()
        if not enabled:
            return

        if db_path is None:
            from kairix.paths import db_path as get_db_path

            db_path = get_db_path()

        from kairix.core.embed.embed import open_usearch_index_for_paths

        # Opens, recovers, logs — no further action needed. The returned
        # VectorIndex isn't kept (the embed tick opens its own).
        open_usearch_index_for_paths(
            index_path=db_path.parent / "vectors.usearch",
            meta_path=db_path.parent / "vectors.meta.json",
            db_path=db_path,
        )
    except Exception as exc:  # pragma: no cover - boundary
        logger.warning(
            "worker: vec_index startup probe raised — %s. "
            "fix: this is non-fatal; first embed tick will retry. "
            "next: kairix onboard check if the warning persists. "
            "run: ls -la $KAIRIX_DOCUMENT_ROOT/../kairix/vectors.usearch*",
            exc,
        )


@dataclass
class TopologyV2ApplyDeps:
    """Injectable dependencies for :func:`apply_topology_v2_at_boot`.

    F6-clean: every field has a ``default_factory`` so production callers
    construct ``TopologyV2ApplyDeps()`` and get the real boundary calls;
    tests construct ``TopologyV2ApplyDeps(config_path_resolver=...,
    db_factory=..., flag_reader=lambda _name: True)`` and pass it as a
    single argument to drive the apply step against a tmp_path-rooted
    config + DB without touching the dev's real vault.

    Fields:
      * ``flag_reader`` — returns the effective value of the named
        feature flag. Default :func:`_default_flag_value`. Tests pass a
        lambda returning a deterministic bool to pin the apply path
        independently of the global registry / env state.
      * ``config_path_resolver`` — returns the ``kairix.config.yaml``
        path or ``None``. Default :func:`_resolve_config_path_default`.
      * ``db_factory`` — opens the SQLite connection the apply step
        writes through; default :func:`kairix.core.db.open_db`.
    """

    flag_reader: Callable[[str], bool] = field(default_factory=lambda: _default_flag_value)
    config_path_resolver: Callable[[], Path | None] = field(default_factory=lambda: _resolve_config_path_default)
    db_factory: Callable[[], sqlite3.Connection] = field(default_factory=lambda: _open_db_default)


def apply_topology_v2_at_boot(deps: TopologyV2ApplyDeps | None = None) -> None:
    """Materialise the operator's ``topology_v2:`` YAML into runtime rows.

    Gated on the ``topology_v2_config`` feature flag — flag OFF makes
    the function a structural no-op (returns without opening the DB),
    preserving bit-for-bit pre-Wave-D boot behaviour. Flag ON: loads
    the parsed config, validates cross-references, and calls
    :func:`kairix.core.connectors.topology_v2_applier.apply_topology_v2`
    against the shared SQLite connection.

    Returns None unconditionally — failures are logged but never crash
    the boot. The worker continues so the operator can fix the config
    without crashlooping; the cc_pair lookup in
    :func:`resolve_chunk_writer_for_entry` falls back to the legacy
    writer when no cc_pair has been registered, so a failed apply
    degrades gracefully.
    """
    deps = deps if deps is not None else TopologyV2ApplyDeps()
    if not deps.flag_reader("topology_v2_config"):
        return

    config_path = deps.config_path_resolver()
    if config_path is None or not config_path.exists():
        logger.info("worker: topology_v2 apply skipped — no kairix.config.yaml on disk")
        return

    import yaml

    from kairix.config import parse_topology_v2
    from kairix.core.connectors.topology_v2_applier import (
        ApplyValidationError,
        apply_topology_v2,
    )
    from kairix.core.db.schema import create_schema

    try:
        with config_path.open() as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("worker: topology_v2 apply skipped — could not read config: %s", exc)
        return

    try:
        parsed = parse_topology_v2(raw)
    except Exception as exc:
        logger.warning("worker: topology_v2 apply skipped — parse failure: %s", exc)
        return

    if not (parsed.connectors or parsed.credentials or parsed.cc_pairs or parsed.collections):
        logger.info("worker: topology_v2 apply skipped — no blocks declared in config")
        return

    db = deps.db_factory()
    try:
        create_schema(db)
        try:
            result = apply_topology_v2(db, parsed)
        except ApplyValidationError as exc:
            logger.warning("worker: topology_v2 apply rejected — %s", exc)
            db.rollback()
            return
        db.commit()
    finally:
        db.close()

    logger.info(
        "worker: topology_v2 applied — created=%d updated=%d unchanged=%d",
        result.created,
        result.updated,
        result.unchanged,
    )


def _boot_state(deps: WorkerDeps) -> WorkerState:
    """Load prior state from disk (increment restart_count) or start fresh.

    #224 phase 5: if a prior run left a state file, we INCREMENT its
    ``restart_count`` and reuse historical counters so operators see
    lifetime totals across restarts.
    """
    prior = deps.read_state_fn(deps.state_path)
    if prior is not None:
        prior.restart_count += 1
        logger.info("worker: resumed from prior state — restart_count=%d", prior.restart_count)
        return prior
    logger.info("worker: no prior state on disk — starting fresh")
    return deps.state


def _apply_embed_outcome(state: WorkerState, outcome: EmbedRunOutcome, consecutive_noops: int) -> int:
    """Fold an embed outcome into worker state; return the updated no-op streak."""
    new_streak = 0 if outcome.did_work else consecutive_noops + 1
    state.consecutive_embed_noops = new_streak
    state.embedded_total += outcome.embedded
    state.failed_chunks_total += outcome.failed
    state.last_embed_run_at = time.time()
    state.last_embed_did_work = outcome.did_work
    if outcome.recall_passed is False:
        state.recall_alerts_total += 1
    return new_streak


def _check_paused(deps: WorkerDeps, transition: Callable[[WorkerPhase], None], previously_paused: bool) -> bool:
    """Handle the operator-pause flag. Returns the new ``previously_paused`` value.

    When the flag is present we sleep and return True; otherwise we restore
    IDLE phase if we were paused and return False.
    """
    if deps.pause_flag_path.exists():
        if not previously_paused:
            transition(WorkerPhase.PAUSED)
            logger.info("worker: paused — flag file present at %s", deps.pause_flag_path)
        deps.sleep(PAUSE_POLL_INTERVAL_S)
        return True
    if previously_paused:
        transition(WorkerPhase.IDLE)
        logger.info("worker: resumed — flag file removed")
    return False


def _log_maintenance_toggle(maintenance_active: bool, previously_skipping: bool, streak: int) -> bool:
    """Log skip-enter / skip-exit transitions; return the new ``previously_skipping`` flag."""
    if not maintenance_active and not previously_skipping:
        logger.info(
            "worker: skipping maintenance scans — %d consecutive no-op embeds (threshold %d)",
            streak,
            MAINTENANCE_SKIP_NOOP_THRESHOLD,
        )
        return True
    if maintenance_active and previously_skipping:
        logger.info("worker: maintenance scans resumed — embed found work")
        return False
    return previously_skipping


def _run_embed_cycle(
    deps: WorkerDeps,
    state: WorkerState,
    transition: Callable[[WorkerPhase], None],
    streak: int,
) -> int:
    """Run one embed pass, persist state, log idle-backoff if applicable. Returns new streak."""
    transition(WorkerPhase.INGEST)
    outcome = run_embed_with_outcome(deps)
    new_streak = _apply_embed_outcome(state, outcome, streak)
    transition(WorkerPhase.IDLE)
    return new_streak


def _run_maintenance_task(
    deps: WorkerDeps,
    transition: Callable[[WorkerPhase], None],
    task: Callable[[WorkerDeps], None],
) -> None:
    """Run one maintenance task with MAINTENANCE→IDLE phase transitions."""
    transition(WorkerPhase.MAINTENANCE)
    task(deps)
    transition(WorkerPhase.IDLE)


def _maybe_run_maintenance_cycle(
    *,
    deps: WorkerDeps,
    transition: Callable[[WorkerPhase], None],
    now: float,
    maintenance_active: bool,
    last_entity: float,
    last_health: float,
    last_wikilinks: float,
    last_connector_sync: float,
    last_neo4j_drain: float,
    schedule: _Schedule,
) -> tuple[float, float, float, float, float]:
    """Run any maintenance task whose interval has elapsed; return updated timestamps.

    Two buckets (#312):

    * **Local-content-dependent** (entity, health, wikilinks) — gated by
      ``maintenance_active``. When the local vault has been idle long
      enough to set the embed-noop streak above the threshold, none of
      these have anything to do and the maintenance scan is wasted work.

    * **External-source-discovery** (connector_sync) AND **Curator
      coupling boundary** (neo4j_drain, GH #334) — ALWAYS run on their
      intervals regardless of ``maintenance_active``. A quiet local
      vault does NOT imply quiet upstream sources or a drained
      ``entity_signals`` queue.
    """
    new_entity, new_health, new_wikilinks = last_entity, last_health, last_wikilinks
    if maintenance_active:
        local_tasks = (
            ("entity", schedule.entity, last_entity, run_entity_seed),
            ("health", schedule.health, last_health, run_health_check),
            ("wikilinks", schedule.wikilinks, last_wikilinks, run_wikilinks_inject),
        )
        new_local: dict[str, float] = {
            "entity": last_entity,
            "health": last_health,
            "wikilinks": last_wikilinks,
        }
        for name, interval, last_run, task in local_tasks:
            if now - last_run >= interval:
                _run_maintenance_task(deps, transition, task)
                new_local[name] = now
        new_entity, new_health, new_wikilinks = new_local["entity"], new_local["health"], new_local["wikilinks"]

    new_connector_sync = last_connector_sync
    if now - last_connector_sync >= schedule.connector_sync:
        _run_maintenance_task(deps, transition, run_connector_sync)
        new_connector_sync = now

    new_neo4j_drain = last_neo4j_drain
    if now - last_neo4j_drain >= schedule.neo4j_drain:
        _run_maintenance_task(deps, transition, run_neo4j_drain)
        new_neo4j_drain = now

    return (new_entity, new_health, new_wikilinks, new_connector_sync, new_neo4j_drain)


def main(
    *,
    deps: WorkerDeps | None = None,
    embed_interval: int | None = None,
    entity_seed_interval: int | None = None,
    health_check_interval: int | None = None,
    wikilinks_interval: int | None = None,
    connector_sync_interval: int | None = None,
    neo4j_drain_interval: int | None = None,
) -> None:
    """Run the worker loop.

    All callable dependencies are bundled into ``WorkerDeps``;
    interval ints stay as plain kwargs because they're scalar
    config (not test-substitution seams). Production omits ``deps``
    and the default factory wires the real task callables.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    deps = deps if deps is not None else WorkerDeps()
    schedule = _resolve_schedule(
        embed_interval,
        entity_seed_interval,
        health_check_interval,
        wikilinks_interval,
        connector_sync_interval,
        neo4j_drain_interval,
    )

    logger.info(
        "kairix worker starting — embed every %ds, entity seed every %ds, wikilinks every %ds, neo4j drain every %ds",
        schedule.embed,
        schedule.entity,
        schedule.wikilinks,
        schedule.neo4j_drain,
    )

    # Preflight integrity audit — catches the IM-6 failure mode (FTS
    # silently empty) before the first embed cycle. Logs the report at
    # boot; ``KAIRIX_PREFLIGHT_STRICT=1`` makes error gaps fatal.
    if not _run_preflight_at_boot():
        return

    # Vec-index startup probe — surfaces any pending .tmp promotion or
    # corrupt-file recreation BEFORE the first embed tick runs. Without
    # this, operators learnt about recovery actions hours later via the
    # next recall canary (the 2026-05-31 production bug). The probe
    # itself is idempotent — load_or_recreate either passes through an
    # already-valid index or fixes it in place.
    probe_vec_index_at_boot()

    # Wave D apply-bridge — when the topology_v2_config flag is ON, read
    # the parsed config and materialise it into runtime topology_* rows
    # before the first sync tick. Flag OFF: this is a structural no-op
    # (the function short-circuits before opening the DB). Failures
    # degrade gracefully — the legacy single-collection writer remains
    # the fallback in resolve_chunk_writer_for_entry.
    apply_topology_v2_at_boot()

    state = _boot_state(deps)
    # Persist initial state (STARTING) so ``kairix worker status`` is
    # answerable immediately after boot, before the first embed completes.
    state.current_phase = WorkerPhase.STARTING
    state.last_phase_change_at = time.time()
    deps.write_state_fn(state, deps.state_path)

    def _transition(phase: WorkerPhase) -> None:
        """Update state's phase + timestamp and persist atomically.

        Each call is a single write — the persistence layer's temp-file +
        rename keeps concurrent ``kairix worker status`` readers safe.
        """
        state.current_phase = phase
        state.last_phase_change_at = time.time()
        deps.write_state_fn(state, deps.state_path)

    # Track when each task last ran
    last_embed = 0.0
    last_entity = 0.0
    last_health = 0.0
    last_wikilinks = 0.0
    last_connector_sync = 0.0
    # GH #334 — last Neo4j drain tick. Starts at 0.0 so the first
    # post-boot iteration drains immediately (matches the connector_sync
    # bootstrap convention).
    last_neo4j_drain = 0.0
    # KFEAT-021 — last maintenance tick. Carried in WorkerState across
    # restarts so the cadence survives a container bounce; mirror it
    # into a local for the in-loop is_tick_due comparison.
    last_maintenance_tick = state.last_maintenance_tick_at
    maintenance_interval = maintenance_interval_seconds()

    # #224 idle backoff: extend the embed interval after consecutive
    # no-op runs to avoid steady CPU/I/O pressure on idle vaults.
    consecutive_embed_noops = state.consecutive_embed_noops

    # Graceful shutdown
    running = True

    def _shutdown(_signum: int, _frame: object) -> None:
        """Signal handler — flips ``running`` to False on SIGTERM/SIGINT.

        ``_signum``/``_frame`` are the standard signal-callback positional
        slots required by ``signal.signal`` (F19: underscore-prefixed).
        """
        nonlocal running
        logger.info("worker: shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Run embed immediately on startup
    consecutive_embed_noops = _run_embed_cycle(deps, state, _transition, consecutive_embed_noops)
    last_embed = time.monotonic()

    # #224 phase 4: one-shot log on pause/resume so we don't spam every 5s.
    previously_paused = False
    # #224 phase 2: same one-shot-log pattern for maintenance-skip episodes.
    previously_skipping_maint = False

    while running:
        previously_paused = _check_paused(deps, _transition, previously_paused)
        if previously_paused:
            continue

        now = time.monotonic()
        effective_embed_interval = compute_embed_interval(schedule.embed, consecutive_embed_noops)

        if now - last_embed >= effective_embed_interval:
            if effective_embed_interval != schedule.embed:
                logger.info(
                    "worker: idle backoff active — embed interval extended to %ds after %d no-op cycle(s)",
                    effective_embed_interval,
                    consecutive_embed_noops,
                )
            consecutive_embed_noops = _run_embed_cycle(deps, state, _transition, consecutive_embed_noops)
            last_embed = now

        # #224 phase 2 — skip-on-noop maintenance gating. After
        # MAINTENANCE_SKIP_NOOP_THRESHOLD consecutive no-op embed cycles
        # the three maintenance scans become pointless work. Embed continues
        # on its (already exponentially-backed-off) cadence, so a single
        # fresh document still resumes everything.
        maintenance_active = consecutive_embed_noops < MAINTENANCE_SKIP_NOOP_THRESHOLD
        previously_skipping_maint = _log_maintenance_toggle(
            maintenance_active, previously_skipping_maint, consecutive_embed_noops
        )

        (
            last_entity,
            last_health,
            last_wikilinks,
            last_connector_sync,
            last_neo4j_drain,
        ) = _maybe_run_maintenance_cycle(
            deps=deps,
            transition=_transition,
            now=now,
            maintenance_active=maintenance_active,
            last_entity=last_entity,
            last_health=last_health,
            last_wikilinks=last_wikilinks,
            last_connector_sync=last_connector_sync,
            last_neo4j_drain=last_neo4j_drain,
            schedule=schedule,
        )

        # KFEAT-021 — maintenance-loop tick after the sync cycle. The
        # flag check is the OUTER gate (no DB open when OFF); cadence
        # is the INNER gate. Bit-for-bit pre-KFEAT-021 behaviour when
        # the flag is OFF.
        last_maintenance_tick = maybe_run_maintenance_loop_tick(
            deps=deps.maintenance_loop_deps,
            transition=_transition,
            state=state,
            state_path=deps.state_path,
            write_state_fn=deps.write_state_fn,
            now=time.time(),
            last_tick_at=last_maintenance_tick,
            interval_seconds=maintenance_interval,
        )

        # Sleep 60 seconds between checks
        for _ in range(60):
            if not running:
                break
            deps.sleep(1)

    logger.info("kairix worker stopped")


if __name__ == "__main__":
    main()
