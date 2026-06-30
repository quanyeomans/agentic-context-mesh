"""Incremental embed pipeline — the use case the CLI and worker both call.

This module hosts the canonical "scan → embed → recall gate" flow as a
single function (``run_incremental_embed_pipeline``) that returns a
structured ``EmbedPipelineResult``. Two consumers:

  - ``kairix embed`` (CLI) maps the result to a process exit code.
  - ``kairix worker`` (background daemon) calls the function directly,
    inspects the result, logs alerts, and continues to the next interval.

This use case exists to keep the recall gate's job (fire an alert) and
the worker's job (stay alive and run on a schedule) communicating via
data flow rather than process semantics: the gate's outcome is reported
in the returned dataclass and the caller decides what to do, instead of
the recall-gate code raising ``SystemExit`` and tearing down the worker
(``except Exception`` does not catch ``SystemExit``).

Design notes:
  - The recall gate is an **alert**, not a fatal error. The use case
    runs the gate (unless ``skip_recall_check=True``) and reports the
    score in the result dataclass. Callers decide what to do.
  - Embed failures (chunks that errored at the Azure boundary) are
    counted in ``failed`` and are retryable on the next run.
  - Schema, scan, FTS rebuild are all in-flow. Splitting them behind
    extra abstractions adds no value here — they always run together.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import DEFAULT_BATCH_SIZE, DEFAULT_PARALLEL_BATCHES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Production defaults — lazy-import wrappers used by ``PipelineDeps``'
# ``default_factory`` slots. Each helper is a thin pass-through to the
# real implementation; lazy ``from kairix.<pkg> import ...`` calls keep
# import-time cost off any unit test that injects stand-ins.
#
# Each wrapper accepts an optional ``UseCaseDeps`` so unit tests can
# pin the wrapped impls (and the scan-composition collaborators) via
# DI rather than ``monkeypatch.setattr`` on kairix modules (F1).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UseCaseDeps:
    """Injection seam for the lazy-import wrappers in this module.

    Production callers leave every field unset; the ``default_factory``
    slots wire the canonical kairix implementations on first call.
    Tests construct ``UseCaseDeps(get_db_path_fn=..., DocumentScanner=...)``
    and pass ``deps=`` to the ``default_*`` wrappers to drive each
    branch without monkey-patching ``kairix.core.db`` / ``kairix.core.embed.*``
    / ``kairix.core.search.*`` / ``kairix.paths``.

    Fields are bundled into one dataclass — not split per-wrapper — so
    ``default_scan_documents`` can compose all seven collaborators
    (DocumentScanner, load_collections, resolve_config_path, agent
    registry, reference-library probe, FTS rebuild, yaml loader)
    through a single deps argument the test constructs once.
    """

    # default_db_path / default_open_db
    get_db_path_fn: Callable[[], Path] = field(default_factory=lambda: _real_get_db_path)
    open_db_fn: Callable[[Path], Any] = field(default_factory=lambda: _real_open_db)
    # default_create_schema / default_validate_schema
    create_schema_fn: Callable[[Any], None] = field(default_factory=lambda: _real_create_schema)
    validate_schema_fn: Callable[[Any], None] = field(default_factory=lambda: _real_validate_schema)
    # default_acquire_lock / default_release_lock
    acquire_lock_fn: Callable[[], Any] = field(default_factory=lambda: _real_acquire_lock)
    release_lock_fn: Callable[[Any], None] = field(default_factory=lambda: _real_release_lock)
    # default_save_run_log
    save_run_log_fn: Callable[[dict[str, Any]], None] = field(default_factory=lambda: _real_save_run_log)
    # default_run_embed / default_run_recall_gate
    run_embed_fn: Callable[..., dict[str, Any]] = field(default_factory=lambda: _real_run_embed)
    run_recall_gate_fn: Callable[..., tuple[bool, dict[str, Any]]] = field(
        default_factory=lambda: _real_run_recall_gate
    )
    # default_scan_documents composes these seven collaborators
    document_scanner_cls: Callable[..., Any] = field(default_factory=lambda: _real_document_scanner)
    load_collections_fn: Callable[[], Any] = field(default_factory=lambda: _real_load_collections)
    resolve_config_path_fn: Callable[[], Any] = field(default_factory=lambda: _real_resolve_config_path)
    parse_agent_registry_fn: Callable[..., Any] = field(default_factory=lambda: _real_parse_agent_registry)
    build_agent_owner_resolver_fn: Callable[[Any], Any] = field(
        default_factory=lambda: _real_build_agent_owner_resolver
    )
    document_root_fn: Callable[[], Path] = field(default_factory=lambda: _real_document_root)
    reference_library_root_fn: Callable[[], Path] = field(default_factory=lambda: _real_reference_library_root)
    # reference_library.index mode (#475) — "eager" | "lazy" | "skip".
    reflib_index_mode_fn: Callable[[], str] = field(default_factory=lambda: _real_reflib_index_mode)
    rebuild_fts_fn: Callable[[Any], int] = field(default_factory=lambda: _real_rebuild_fts)
    yaml_safe_load_fn: Callable[[Any], Any] = field(default_factory=lambda: _real_yaml_safe_load)


# Lazy-import accessors — each returns the canonical kairix function on
# demand so importing ``kairix.core.embed.use_cases`` stays cheap.


def _real_get_db_path() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db import get_db_path

    return get_db_path()


def _real_open_db(path: Path) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db import open_db

    return open_db(path)


def _real_create_schema(db: Any) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db.schema import create_schema

    create_schema(db)


def _real_validate_schema(db: Any) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db.schema import validate_schema

    validate_schema(db)


def _real_acquire_lock() -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.embed.cli import acquire_lock

    return acquire_lock()


def _real_release_lock(lock_fh: Any) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.embed.cli import release_lock

    release_lock(lock_fh)


def _real_save_run_log(entry: dict[str, Any]) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.embed.schema import save_run_log

    save_run_log(entry)


def _real_run_embed(**kwargs: Any) -> dict[str, Any]:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.embed.embed import run_embed

    return run_embed(**kwargs)


def _real_run_recall_gate(
    **kwargs: Any,
) -> tuple[bool, dict[str, Any]]:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.embed.recall_check import run_recall_gate

    return run_recall_gate(**kwargs)


def _real_document_scanner(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db.scanner import DocumentScanner

    return DocumentScanner(*args, **kwargs)


def _real_load_collections() -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.search.config_loader import load_collections

    return load_collections()


def _real_resolve_config_path() -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.search.config_loader import resolve_config_path

    return resolve_config_path()


def _real_parse_agent_registry(raw: Any, **kw: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.search.registry import parse_agent_registry

    return parse_agent_registry(raw, **kw)


def _real_build_agent_owner_resolver(registry: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.search.registry import build_agent_owner_resolver

    return build_agent_owner_resolver(registry)


def _real_document_root() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.paths import document_root

    return document_root()


def _real_reference_library_root() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.paths import reference_library_root

    return reference_library_root()


def _real_reflib_index_mode() -> str:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.search.config_loader import load_reference_library

    return load_reference_library().index


def _real_rebuild_fts(db: Any) -> int:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db.fts import rebuild_fts

    return rebuild_fts(db)


def _real_yaml_safe_load(stream: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    import yaml

    return yaml.safe_load(stream)


def default_db_path(*, deps: UseCaseDeps | None = None) -> str:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    return str(d.get_db_path_fn())


def default_open_db(
    path: Path, *, deps: UseCaseDeps | None = None
) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    return d.open_db_fn(path)


def default_create_schema(
    db: Any, *, deps: UseCaseDeps | None = None
) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    d.create_schema_fn(db)


def default_validate_schema(
    db: Any, *, deps: UseCaseDeps | None = None
) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    d.validate_schema_fn(db)


def default_acquire_lock(
    *, deps: UseCaseDeps | None = None
) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    return d.acquire_lock_fn()


def default_release_lock(
    lock_fh: Any, *, deps: UseCaseDeps | None = None
) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    d.release_lock_fn(lock_fh)


def default_save_run_log(
    entry: dict[str, Any], *, deps: UseCaseDeps | None = None
) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    d.save_run_log_fn(entry)


def default_run_embed(
    *, deps: UseCaseDeps | None = None, **kwargs: Any
) -> dict[str, Any]:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    return d.run_embed_fn(**kwargs)


def default_run_recall_gate(
    *, deps: UseCaseDeps | None = None, **kwargs: Any
) -> tuple[bool, dict[str, Any]]:  # pragma: no cover  # lazy-import DI-default delegation
    d = deps if deps is not None else UseCaseDeps()
    return d.run_recall_gate_fn(**kwargs)


REFERENCE_LIBRARY_NAME = "reference-library"


def harmonise_reference_library(
    declared: list[Any],
    reflib_root: Path,
    document_root: Path,
) -> list[Any]:
    """Reconcile operator-declared reference-library with the bundled path.

    The reference library ships inside the kairix container image at
    ``$KAIRIX_REFLIB_ROOT`` (typically ``/opt/kairix/reference-library``).
    Operators who declare the collection in ``kairix.config.yaml`` for
    custom retrieval params often write ``path: reference-library``
    (relative), which the scanner resolves under
    ``$KAIRIX_DOCUMENT_ROOT`` and fails to find — silent miss with only
    a WARNING. This helper:

    1. If the operator declared ``reference-library`` and its path
       does NOT resolve, rewrite the path to ``reflib_root`` and emit
       an actionable INFO line telling them how to silence the notice.
    2. If the operator declared it with a valid path, leave it alone.
    3. If no declaration exists, append a default declaration (the
       historic behaviour) pointing at ``reflib_root``.

    Net: one and only one ``reference-library`` entry survives, always
    pointing at a path that exists. The user's retrieval params apply
    iff declared.
    """
    from dataclasses import replace as _replace

    if not reflib_root.is_dir():
        # No bundled library available — nothing to harmonise.
        return declared

    out = list(declared)
    found_at: int | None = None
    for idx, c in enumerate(out):
        if getattr(c, "name", None) == REFERENCE_LIBRARY_NAME:
            found_at = idx
            break

    if found_at is None:
        out.append(default_reflib_collection(reflib_root))
        return out

    c = out[found_at]
    declared_path = Path(c.path) if Path(c.path).is_absolute() else document_root / c.path
    if declared_path.is_dir():
        return out  # Operator's path resolves — respect their declaration verbatim.

    logger.info(
        "use_cases: auto-correcting '%s' path %s -> %s "
        "(declared path does not resolve; falling back to KAIRIX_REFLIB_ROOT). "
        "fix: in your kairix.config.yaml, change `path: %s` to `path: %s` to silence this notice. "
        "next: kairix config validate. "
        "run: docker compose restart kairix kairix-worker",
        REFERENCE_LIBRARY_NAME,
        declared_path,
        reflib_root,
        c.path,
        reflib_root,
    )
    out[found_at] = _replace(c, path=str(reflib_root))
    return out


def default_reflib_collection(reflib_root: Path) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.db.scanner import CollectionConfig

    return CollectionConfig(name=REFERENCE_LIBRARY_NAME, path=str(reflib_root), glob="**/*.md")


def _resolve_reflib_collections(
    scan_collections: list[Any],
    d: UseCaseDeps,
    droot: Path,
) -> list[Any]:
    """Apply the ``reference_library.index`` mode to the scan walk (#475).

    ``skip`` excludes the bundled reference library from the walk
    entirely (and drops an operator-declared entry of the same name).
    ``eager`` / ``lazy`` keep today's harmonise behaviour — the embed
    gather applies the lazy deferral downstream.
    """
    if d.reflib_index_mode_fn() != "skip":
        return harmonise_reference_library(scan_collections, d.reference_library_root_fn(), droot)
    kept = [c for c in scan_collections if getattr(c, "name", None) != REFERENCE_LIBRARY_NAME]
    logger.info("reference-library: index mode 'skip' — bundled library excluded from the scan walk")
    return kept


def _build_scanner(  # pragma: no cover  # lazy-import DI-default delegation
    db: Any,
    diagnostics: list[str],
    d: UseCaseDeps,
) -> tuple[Any, list[Any]]:
    """Construct the DocumentScanner + resolved scan-collection list.

    Shared by :func:`default_scan_documents` (full-tree walk) and
    :func:`default_index_file` (single-file index) so the agent-resolver
    wiring and the collection-resolution (config collections → default
    fallback → reference-library mode) live in ONE place. Returns
    ``(scanner, scan_collections)``.
    """
    from kairix.core.db.scanner import CollectionConfig

    droot = d.document_root_fn()

    agent_resolver = None
    try:
        config_path = d.resolve_config_path_fn()
        if config_path is not None:
            with config_path.open(encoding="utf-8") as _f:
                _raw_yaml = d.yaml_safe_load_fn(_f) or {}
            _registry = d.parse_agent_registry_fn(_raw_yaml)
            if _registry.list_agents():
                agent_resolver = d.build_agent_owner_resolver_fn(_registry)
    # Agent-resolver construction is best-effort; we'd rather scan with
    # agent_owner=NULL than skip the scan entirely.
    except Exception as exc:
        diagnostics.append(f"agent_resolver_unavailable: {exc}")

    scanner = d.document_scanner_cls(db, document_root=droot, agent_owner_resolver=agent_resolver)

    collections_cfg = d.load_collections_fn()
    if collections_cfg and collections_cfg.shared:
        scan_collections = [CollectionConfig(name=c.name, path=c.path, glob=c.glob) for c in collections_cfg.shared]
        logger.info("Using %d configured collections", len(scan_collections))
    else:
        scan_collections = [CollectionConfig(name="default", path=".")]

    scan_collections = _resolve_reflib_collections(scan_collections, d, droot)
    return scanner, scan_collections


def default_scan_documents(  # pragma: no cover  # lazy-import DI-default delegation
    db: Any,
    diagnostics: list[str],
    *,
    deps: UseCaseDeps | None = None,
) -> tuple[int, int, int]:
    """Scan the document root for new/changed files and rebuild FTS.

    Lives in the use-case module so ``PipelineDeps``' default factory
    can wire it directly. ``deps`` injects every collaborator
    (DocumentScanner, load_collections, resolve_config_path, agent
    registry, reference-library probe, FTS rebuild, yaml loader) so
    unit tests construct one ``UseCaseDeps(...)`` and drive every
    branch without monkey-patching kairix modules.
    """
    d = deps if deps is not None else UseCaseDeps()
    scanner, scan_collections = _build_scanner(db, diagnostics, d)

    scan_report = scanner.scan(scan_collections)
    if scan_report.new > 0 or scan_report.updated > 0:
        logger.info(
            "Scanned documents: %d new, %d updated, %d unchanged",
            scan_report.new,
            scan_report.updated,
            scan_report.unchanged,
        )
        fts_count = d.rebuild_fts_fn(db)
        logger.info("FTS index rebuilt: %d documents", fts_count)

    return scan_report.new, scan_report.updated, scan_report.errors


def default_index_file(  # pragma: no cover  # lazy-import DI-default delegation
    db: Any,
    diagnostics: list[str],
    file_path: Path,
    *,
    deps: UseCaseDeps | None = None,
) -> tuple[int, int, int]:
    """Incrementally index ONE file — the latency-sensitive write path (PLA-258).

    Mirrors :func:`default_scan_documents` but processes only ``file_path``
    (the file a ``remember`` write just produced) via the scanner's
    single-file path, then runs an INCREMENTAL FTS update for exactly the
    touched rows (:func:`kairix.core.db.fts.sync_fts`) — never a full
    rescan or full FTS rebuild. The whole document tree is left untouched,
    so the memory-write cost stays O(1) in corpus size. Returns
    ``(new, updated, errors)`` for parity with the full scan.
    """
    from kairix.core.db.fts import sync_fts

    d = deps if deps is not None else UseCaseDeps()
    scanner, scan_collections = _build_scanner(db, diagnostics, d)

    scan_report, touched = scanner.scan_file(file_path, scan_collections)
    if touched:
        synced = sync_fts(db, touched)
        db.commit()
        logger.info(
            "Indexed single file: %d new, %d updated; FTS synced %d documents",
            scan_report.new,
            scan_report.updated,
            synced,
        )

    return scan_report.new, scan_report.updated, scan_report.errors


@dataclass(frozen=True)
class EmbedPipelineResult:
    """Outcome of one ``run_incremental_embed_pipeline`` invocation.

    Attributes:
        embedded: Chunks newly embedded this run.
        failed: Chunks the Azure call failed for. Retried automatically
            on the next run unless ``--force`` is passed.
        skipped: Chunks where the document was already up-to-date.
        duration_s: Wall-clock seconds spent embedding.
        cost_usd: Estimated cost of this run.
        db_path: Absolute path to the SQLite database.
        timestamp: Unix epoch start of this run.
        recall_score: Fraction (0..1) of canary queries that hit. None
            if the recall gate was skipped.
        recall_passed: Whether the recall gate's degradation check
            passed. None if the gate was skipped. False means an alert
            was logged — NOT a fatal error.
        recall_alert: Human-readable alert message when
            ``recall_passed=False``; None otherwise.
        scan_new / scan_updated / scan_errors: Document scan counters.
        diagnostics: Best-effort messages from sub-steps that may have
            partially failed without aborting the pipeline (e.g. agent
            resolver unavailable, recall gate raised).
    """

    embedded: int
    failed: int
    skipped: int
    duration_s: float
    cost_usd: float
    db_path: str
    timestamp: int
    recall_score: float | None = None
    recall_passed: bool | None = None
    recall_alert: str | None = None
    scan_new: int = 0
    scan_updated: int = 0
    scan_errors: int = 0
    diagnostics: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Whether the embed pass itself succeeded (no chunks failed).

        Recall-gate failures are NOT counted here — those are alerts,
        and ``recall_passed`` exposes them separately.
        """
        return self.failed == 0


@dataclass(frozen=True)
class PipelineDeps:
    """Injectable dependencies for ``run_incremental_embed_pipeline``.

    Production callers leave every field unset and the dataclass'
    ``default_factory`` wires the real implementations. Tests construct
    a ``PipelineDeps(...)`` with light-weight stand-ins to drive the
    orchestration end-to-end without touching real DB / Azure / disk.

    All fields are intentionally callables (not concrete instances) so
    the use case stays decoupled from import order and module-level
    state in the production helpers.

    All callable fields use ``field(default_factory=lambda: _default_X)``
    rather than ``Callable[...] | None = None`` (per CLAUDE.md F6
    guidance: avoid the ``Optional[Callable] + post-init`` pattern) so
    mypy sees the production callable directly and
    ``run_incremental_embed_pipeline`` invokes ``pdeps.x_fn(...)``
    without a None-fallback ladder.
    """

    db_path_fn: Callable[[], str] = field(default_factory=lambda: default_db_path)
    open_db_fn: Callable[[Path], Any] = field(default_factory=lambda: default_open_db)
    schema_fn: Callable[[Any], None] = field(default_factory=lambda: default_create_schema)
    validate_schema_fn: Callable[[Any], None] = field(default_factory=lambda: default_validate_schema)
    acquire_lock_fn: Callable[[], Any] = field(default_factory=lambda: default_acquire_lock)
    release_lock_fn: Callable[[Any], None] = field(default_factory=lambda: default_release_lock)
    save_run_log_fn: Callable[[dict[str, Any]], None] = field(default_factory=lambda: default_save_run_log)
    run_embed_fn: Callable[..., dict[str, Any]] = field(default_factory=lambda: default_run_embed)
    run_recall_gate_fn: Callable[..., tuple[bool, dict[str, Any]]] = field(
        default_factory=lambda: default_run_recall_gate
    )
    scan_documents_fn: Callable[[Any, list[str]], tuple[int, int, int]] = field(
        default_factory=lambda: default_scan_documents
    )


def _drop_embedding_cache(deps: EmbedDependencies | None, diagnostics: list[str]) -> None:
    """Clear the persistent embedding cache before the embed call.

    Opens a transient handle via ``deps.open_embedding_cache`` (the same
    seam ``run_embed`` uses) so production reads from
    :func:`kairix.paths.embedding_cache_path` and tests can inject a
    pinned ``EmbeddingCache(tmp_path)``. Failures are surfaced as
    diagnostics rather than raised so the embed call still runs — the
    operator picked ``--force-rebuild-cache`` because they want to
    re-embed regardless.
    """
    if deps is None:  # pragma: no cover  # production-default; tests inject deps=EmbedDependencies(...)
        deps = EmbedDependencies()
    try:
        cache = deps.open_embedding_cache()
    except Exception as exc:  # pragma: no cover  # defensive — open failures observed only at integration scale
        diagnostics.append(f"force_rebuild_cache: open failed — {exc}")
        return
    if cache is None:  # pragma: no cover  # cache-layer-unavailable surface (CacheLoaderRegistry off)
        diagnostics.append("force_rebuild_cache: open returned None — cache layer unavailable")
        return
    try:
        cache.clear()
    except Exception as exc:  # pragma: no cover  # defensive — clear failures only at IO boundary
        diagnostics.append(f"force_rebuild_cache: clear failed — {exc}")
    finally:
        cache.close()


def run_incremental_embed_pipeline(
    *,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    skip_recall_check: bool = False,
    rebuild_canaries: bool = False,
    deps: EmbedDependencies | None = None,
    pipeline_deps: PipelineDeps | None = None,
    parallel: int = DEFAULT_PARALLEL_BATCHES,
    force_rebuild_cache: bool = False,
) -> EmbedPipelineResult:
    """Run the full incremental embed pipeline and return a structured result.

    The pipeline:

      1. Acquire the embed lock (so we don't run two embeds concurrently).
      2. Open the SQLite DB; ensure schema exists.
      3. Scan the document root for new / changed files.
      4. Rebuild the FTS index when the scan saw any new or updated doc.
      5. Run ``run_embed`` over the pending chunks.
      6. (Optional) Run the recall gate. The gate's outcome is captured
         in the result dataclass; failures are alerts, not exceptions.

    Raises only on truly unrecoverable conditions (DB unreachable,
    schema migration failure). All other failure modes — Azure errors,
    recall regression, scan errors — are reported in the result.

    ``deps`` injects embed-stage dependencies (Azure config, batch I/O).
    ``pipeline_deps`` injects orchestration dependencies (DB, lock,
    scan, recall) — used by tests to drive the full flow without
    touching production disk or Azure. Production callers leave both
    None and lazy production defaults are wired on demand.
    """
    pdeps = pipeline_deps or PipelineDeps()

    db_path_fn = pdeps.db_path_fn
    open_db_fn = pdeps.open_db_fn
    schema_fn = pdeps.schema_fn
    validate_fn = pdeps.validate_schema_fn
    acquire_fn = pdeps.acquire_lock_fn
    release_fn = pdeps.release_lock_fn
    save_log_fn = pdeps.save_run_log_fn
    embed_fn = pdeps.run_embed_fn
    recall_fn = pdeps.run_recall_gate_fn
    scan_fn = pdeps.scan_documents_fn

    diagnostics: list[str] = []

    logger.info(
        "embed pipeline starting — force=%s limit=%s batch_size=%s",
        force,
        limit,
        batch_size,
    )

    lock_fh = acquire_fn()
    db_path = db_path_fn()
    start = time.time()
    embed_result: dict[str, Any]

    try:
        db = open_db_fn(Path(db_path))
        try:
            schema_fn(db)
            validate_fn(db)

            scan_new, scan_updated, scan_errors = scan_fn(db, diagnostics)

            if (
                force_rebuild_cache
            ):  # pragma: no cover  # operator-flag branch; exercised by run_incremental_embed_pipeline integration
                _drop_embedding_cache(deps, diagnostics)

            embed_result = embed_fn(
                db=db,
                force=force,
                batch_size=batch_size,
                limit=limit,
                deps=deps,
                parallel=parallel,
            )
            embed_result["command"] = "embed"
            embed_result["db_path"] = str(db_path)
            embed_result["timestamp"] = int(start)
            save_log_fn(embed_result)
        finally:
            db.close()
    finally:
        release_fn(lock_fh)

    recall_score: float | None = None
    recall_passed: bool | None = None
    recall_alert: str | None = None

    if not skip_recall_check:
        captured_alert: list[str] = []

        def _capture(msg: str) -> None:
            captured_alert.append(msg)

        try:
            recall_passed, recall_result = recall_fn(
                alert_callback=_capture,
                rebuild_canaries=rebuild_canaries,
            )
            recall_score = float(recall_result.get("score", 0.0))
            if captured_alert:
                recall_alert = captured_alert[0]
        # The recall gate is best-effort; swallowing its errors keeps the
        # caller's primary signal (embed result) intact. The diagnostic
        # is captured so operators can still see what went wrong.
        except Exception as exc:
            logger.warning("recall gate failed to run: %s", exc)
            diagnostics.append(f"recall_gate_error: {exc}")

    return EmbedPipelineResult(
        embedded=int(embed_result.get("embedded", 0)),
        failed=int(embed_result.get("failed", 0)),
        skipped=int(embed_result.get("skipped", 0)),
        duration_s=float(embed_result.get("duration_s", 0)),
        cost_usd=float(embed_result.get("estimated_cost_usd", 0.0)),
        db_path=str(db_path),
        timestamp=int(start),
        recall_score=recall_score,
        recall_passed=recall_passed,
        recall_alert=recall_alert,
        scan_new=scan_new,
        scan_updated=scan_updated,
        scan_errors=scan_errors,
        diagnostics=diagnostics,
    )
