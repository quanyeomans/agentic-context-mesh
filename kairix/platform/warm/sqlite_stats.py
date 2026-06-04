"""Warm-step: ensure SQLite query-planner statistics are present.

A fresh kairix install bootstraps schema + ingests its corpus before the
first agent query ever lands. SQLite's query planner relies on
``sqlite_stat1`` (populated by ``ANALYZE``) to pick the right index for
hot-path queries like::

    SELECT ... FROM documents WHERE collection=? AND active=1

Without stats, the planner falls back to heuristics and on production
data (~2.17M documents) picks ``idx_documents_active`` (matches every
active row) over ``idx_documents_collection`` (matches the requested
collection only). The 2026-06-02 production audit found this exact
regression — one manual ``ANALYZE`` took 107 seconds and switched the
plan immediately.

This module bootstraps the stats once during warm-up. Idempotent: when
``sqlite_stat1`` is already populated the call is a structural no-op so
operators don't pay 100+ seconds on every container restart.

API:

    from kairix.platform.warm.sqlite_stats import ensure_sqlite_stats
    result = ensure_sqlite_stats(db, paths)
    # result.detail in {"ANALYZE complete", "stats already present, skipped"}

F-rule positioning:
* F1 / F2 — module accepts an open ``sqlite3.Connection`` + ``KairixPaths``
  via the public signature; tests pass tmp-DB connections + FakePaths
  without monkey-patching or env-var manipulation.
* F4 — no ``KAIRIX_*`` env reads; the connection is the input contract.
* F42 — :class:`WarmStepResult` is a frozen dataclass with explicit
  field types (no ``dict[str, Any]``).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from kairix.paths import KairixPaths

logger = logging.getLogger(__name__)

__all__ = [
    "DETAIL_ANALYZE_COMPLETE",
    "DETAIL_SKIPPED_STATS_PRESENT",
    "STEP_NAME",
    "WarmStepResult",
    "ensure_sqlite_stats",
]

STEP_NAME = "ensure_sqlite_stats"

# Detail string literals — extracted so call sites (production + tests)
# share the exact same canonical text and a typo can't drift the contract.
DETAIL_ANALYZE_COMPLETE = "ANALYZE complete"
DETAIL_SKIPPED_STATS_PRESENT = "stats already present, skipped"


@dataclass(frozen=True)
class WarmStepResult:
    """Frozen envelope returned from :func:`ensure_sqlite_stats`.

    F42-clean: explicit fields, no ``dict[str, Any]``. Fields:

    * ``name`` — canonical step name (always ``STEP_NAME``).
    * ``ok`` — True when the step completed without raising. False
      reserved for future expansion when the step grows error modes;
      currently always True since both the analyze + skip paths succeed.
    * ``elapsed_ms`` — wall-clock duration of the step in milliseconds.
      Zero when the step short-circuited (stats already present).
    * ``detail`` — one of :data:`DETAIL_ANALYZE_COMPLETE` /
      :data:`DETAIL_SKIPPED_STATS_PRESENT`. Operators read this to
      distinguish "we ran ANALYZE this boot" from "stats were already
      present".
    """

    name: str
    ok: bool
    elapsed_ms: float
    detail: str


def _has_any_stats(db: sqlite3.Connection) -> bool:
    """Return True when ``sqlite_stat1`` has at least one row.

    SQLite creates the ``sqlite_stat1`` table eagerly when any index is
    declared, but the rows themselves only land after an ``ANALYZE``. A
    table-exists check alone is insufficient — we must verify at least
    one stat row is present before treating the DB as analyzed.
    """
    try:
        row = db.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()
    except sqlite3.OperationalError:
        # Table doesn't exist yet — definitely no stats.
        return False
    return bool(row and int(row[0]) > 0)


def _has_stat1_table(db: sqlite3.Connection) -> bool:
    """Return True when the ``sqlite_stat1`` table itself exists.

    Sqlite_stat1 is auto-created by ``ANALYZE``; a freshly-built schema
    with no analyze run yet has no stat1 table at all.
    """
    row = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'").fetchone()
    return bool(row and int(row[0]) > 0)


def _documents_has_rows(db: sqlite3.Connection) -> bool:
    """Return True when the ``documents`` table has at least one row.

    Running ANALYZE on an empty DB is wasted I/O — the stats it produces
    are zero-row stats that the planner ignores anyway. Defer the bootstrap
    until the first ingest has actually written something.
    """
    try:
        row = db.execute("SELECT COUNT(*) FROM documents LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        # documents table missing — fresh DB with no schema applied; defer.
        return False
    return bool(row and int(row[0]) > 0)


def ensure_sqlite_stats(db: sqlite3.Connection, paths: KairixPaths) -> WarmStepResult:
    """Bootstrap SQLite query-planner statistics on first warm-up.

    Runs ``ANALYZE`` exactly when:
      * the ``sqlite_stat1`` table is missing OR contains no rows, AND
      * the ``documents`` table has at least one row to analyze.

    Otherwise short-circuits with a zero-elapsed result so the warm-up
    sequence isn't billed for stats that are already present.

    Parameters
    ----------
    db:
        An open SQLite connection on the kairix index. The caller owns
        connection lifecycle; this function does not close it.
    paths:
        :class:`KairixPaths` for the current invocation. Unused by the
        current implementation but kept on the signature so future
        diagnostics (e.g. logging which DB was analyzed) can read paths
        without re-resolving and so the warm-step signature stays uniform
        across steps.

    Returns
    -------
    WarmStepResult
        Frozen envelope with ``detail=DETAIL_ANALYZE_COMPLETE`` when
        ANALYZE ran, ``DETAIL_SKIPPED_STATS_PRESENT`` otherwise.

    Notes
    -----
    Production scale benchmarks (2026-06-02 audit):
      * ANALYZE on ~2.17M documents: ~107 seconds, ~30 stat rows.
      * Plan switch on the hot-path query (idx_documents_active ->
        idx_documents_collection): immediate.
    """
    # The paths argument is reserved for future diagnostic hooks; it stays
    # in the signature so all warm steps share the same contract.
    _ = paths

    if _has_stat1_table(db) and _has_any_stats(db):
        return WarmStepResult(
            name=STEP_NAME,
            ok=True,
            elapsed_ms=0.0,
            detail=DETAIL_SKIPPED_STATS_PRESENT,
        )

    if not _documents_has_rows(db):
        # No data to analyze yet — defer. Mark as skipped so operators
        # see "stats not yet bootstrapped" rather than a misleading
        # "analyze complete" envelope on an empty DB.
        return WarmStepResult(
            name=STEP_NAME,
            ok=True,
            elapsed_ms=0.0,
            detail=DETAIL_SKIPPED_STATS_PRESENT,
        )

    t0 = time.perf_counter()
    db.execute("ANALYZE")
    db.commit()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    logger.info(
        "warm step %s: ANALYZE complete elapsed_ms=%.1f",
        STEP_NAME,
        elapsed_ms,
    )
    return WarmStepResult(
        name=STEP_NAME,
        ok=True,
        elapsed_ms=elapsed_ms,
        detail=DETAIL_ANALYZE_COMPLETE,
    )
