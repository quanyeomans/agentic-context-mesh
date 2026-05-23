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

from kairix.paths import (
    connector_sync_disabled,
    data_dir,
    document_root,
    maintenance_skip_noop_threshold,
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
    """Minimal in-process :class:`~kairix.core.connectors.ChunkWriter`.

    Wave-2 IM-3 keeps the worker independent from the legacy
    ``DocumentScanner`` writer surface — there is no production
    ``DocumentsTableWriter`` yet. This writer persists each
    :class:`~kairix.core.protocols.Chunk` against the canonical
    ``documents`` + ``content`` + ``content_vectors`` tables using the
    same shared :class:`sqlite3.Connection` the pipeline drives, so the
    per-batch transaction stays atomic.

    The writer never commits — the caller's per-batch transaction owns
    the commit (matches :class:`FilesystemBronzeStore` discipline).
    Wave 3+ will swap in a richer writer that updates the FTS5 index;
    Wave 2's responsibility is "chunks land in the documents table" so
    operators can prove end-to-end flow on a real vault.
    """

    def __init__(self, db: sqlite3.Connection, collection: str) -> None:
        self._db = db
        self._collection = collection

    def upsert(self, chunks: Sequence[Chunk]) -> int:
        """Persist ``chunks`` to the documents + content + content_vectors tables.

        Each chunk lands as one ``documents`` row keyed by ``(collection,
        path=source_uri+seq)``, one ``content`` row keyed by
        ``content_hash``, and one ``content_vectors`` row carrying the
        chunk sequence. Does NOT commit.
        """
        written = 0
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for seq, chunk in enumerate(chunks):
            path = f"{chunk.source_uri}#{seq}"
            self._db.execute(
                "INSERT OR REPLACE INTO documents "
                "(collection, path, hash, source_name, source_uri, "
                "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
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

    def stage(self, signals: Sequence[EntitySignal]) -> int:
        """Stage entity signals into the ``entity_signals`` table."""
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
    """
    from kairix.core.connectors import (
        ConnectorPipeline,
        CursorStore,
        DeadLetterStore,
        DefaultSilverProcessor,
        FilesystemBronzeStore,
        resolve_connector,
        resolve_extractor,
    )

    name = entry["name"]
    extractor_name = entry.get("extractor", "passthrough")
    connector_factory = resolve_connector(name)
    extractor_factory = resolve_extractor(extractor_name)
    connector = connector_factory(entry.get("config", {}))
    extractor = (
        extractor_factory() if not entry.get("extractor_config") else extractor_factory(**entry["extractor_config"])
    )
    pipeline = ConnectorPipeline(
        db=db,
        bronze=FilesystemBronzeStore(db, bronze_root),
        silver=DefaultSilverProcessor(),
        chunk_writer=_SqliteChunkWriter(db, collection=name),
        entity_graph_sink=_SqliteEntityGraphSink(db),
        cursor_store=CursorStore(db),
        dead_letter=DeadLetterStore(db),
    )
    result = pipeline.run_batch(connector, extractor)
    return result.processed, result.dead_lettered


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
    and ``sys.exit(1)`` on error. ``SystemExit`` does NOT inherit from
    ``Exception``, so catching only ``Exception`` lets a "successful"
    ``sys.exit(0)`` propagate out and terminate the worker process —
    that's the #270 regression where the kairix-worker container exits
    0 every cycle and Docker restarts it on a loop. Same
    ``(Exception, SystemExit)`` discipline as ``run_embed`` and
    ``run_wikilinks_inject``.

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

    Treats every outcome as non-fatal — including ``SystemExit`` —
    for the same reason as ``run_entity_seed``: a maintenance helper
    that calls ``sys.exit`` must not terminate the worker process.
    See #270 for the entity-seed regression and the (Exception,
    SystemExit) tuple discipline the worker enforces at every CLI
    boundary.

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


@dataclass
class _Schedule:
    """Worker task interval config — bundles the cadence ints.

    Scalar config (not a test-injection seam); main() builds this once
    from kwargs + module defaults so the inner loop helpers can pass a
    single value around rather than discrete ``_embed_interval`` ints.
    SC-6 added ``connector_sync`` alongside the four maintenance cadences.
    """

    embed: int
    entity: int
    health: int
    wikilinks: int
    connector_sync: int


def _resolve_schedule(
    embed_interval: int | None,
    entity_seed_interval: int | None,
    health_check_interval: int | None,
    wikilinks_interval: int | None,
    connector_sync_interval: int | None,
) -> _Schedule:
    """Fold kwargs + module defaults into a single ``_Schedule``."""
    return _Schedule(
        embed=embed_interval if embed_interval is not None else EMBED_INTERVAL,
        entity=entity_seed_interval if entity_seed_interval is not None else ENTITY_SEED_INTERVAL,
        health=health_check_interval if health_check_interval is not None else HEALTH_CHECK_INTERVAL,
        wikilinks=wikilinks_interval if wikilinks_interval is not None else WIKILINKS_INTERVAL,
        connector_sync=connector_sync_interval if connector_sync_interval is not None else CONNECTOR_SYNC_INTERVAL,
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
    schedule: _Schedule,
) -> tuple[float, float, float, float]:
    """Run any maintenance task whose interval has elapsed; return updated timestamps.

    Several near-identical "if interval elapsed → run task → record timestamp"
    blocks collapsed into a dispatch loop to keep ``main``'s cognitive
    complexity under the F16 / S3776 limit (#250 follow-up). SC-6 added
    the ``connector_sync`` slot — it inherits the same maintenance-skip
    gating because a long-idle vault implies quiet upstream sources too.
    """
    if not maintenance_active:
        return (last_entity, last_health, last_wikilinks, last_connector_sync)

    tasks = (
        ("entity", schedule.entity, last_entity, run_entity_seed),
        ("health", schedule.health, last_health, run_health_check),
        ("wikilinks", schedule.wikilinks, last_wikilinks, run_wikilinks_inject),
        (_CONNECTOR_SYNC_KEY, schedule.connector_sync, last_connector_sync, run_connector_sync),
    )
    new_times: dict[str, float] = {
        "entity": last_entity,
        "health": last_health,
        "wikilinks": last_wikilinks,
        _CONNECTOR_SYNC_KEY: last_connector_sync,
    }
    for name, interval, last_run, task in tasks:
        if now - last_run >= interval:
            _run_maintenance_task(deps, transition, task)
            new_times[name] = now
    return (new_times["entity"], new_times["health"], new_times["wikilinks"], new_times[_CONNECTOR_SYNC_KEY])


def main(
    *,
    deps: WorkerDeps | None = None,
    embed_interval: int | None = None,
    entity_seed_interval: int | None = None,
    health_check_interval: int | None = None,
    wikilinks_interval: int | None = None,
    connector_sync_interval: int | None = None,
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
    )

    logger.info(
        "kairix worker starting — embed every %ds, entity seed every %ds, wikilinks every %ds",
        schedule.embed,
        schedule.entity,
        schedule.wikilinks,
    )

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

    # #224 idle backoff: extend the embed interval after consecutive
    # no-op runs to avoid steady CPU/I/O pressure on idle vaults.
    consecutive_embed_noops = state.consecutive_embed_noops

    # Graceful shutdown
    running = True

    def _shutdown(signum: int, frame: object) -> None:
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

        last_entity, last_health, last_wikilinks, last_connector_sync = _maybe_run_maintenance_cycle(
            deps=deps,
            transition=_transition,
            now=now,
            maintenance_active=maintenance_active,
            last_entity=last_entity,
            last_health=last_health,
            last_wikilinks=last_wikilinks,
            last_connector_sync=last_connector_sync,
            schedule=schedule,
        )

        # Sleep 60 seconds between checks
        for _ in range(60):
            if not running:
                break
            deps.sleep(1)

    logger.info("kairix worker stopped")


if __name__ == "__main__":
    main()
