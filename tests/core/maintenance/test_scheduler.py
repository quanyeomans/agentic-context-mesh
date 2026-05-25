"""Unit tests for :class:`kairix.core.maintenance.MaintenanceScheduler`.

Covers the three sabotage proofs the KFEAT-021 brief mandates:

1. **Idempotency** — second tick on a clean DB reports orphans_pruned=0.
   Sabotage: drop the ``INSERT OR IGNORE`` guard, second tick raises a
   UNIQUE-constraint error.
2. **Soft-delete retention** — pruned rows survive the first tick.
   Sabotage: drop the ``pruned_at < cutoff`` filter, pruned rows are
   gone after one tick.
3. **Flag-OFF inertness** — covered by the integration + E2E tests that
   exercise the production wrapper; the scheduler itself is only ever
   invoked when the wrapper passes the flag check.

Test discipline:
  * F1 / F2 clean — no monkey-patching, no env-var manipulation.
  * F6 clean — every Deps field has a real default; tests pass a
    populated :class:`MaintenanceSchedulerDeps`.
  * F8 — each test carries the ``@pytest.mark.unit`` marker.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance import (
    EVENT_TICK_COMPLETED,
    EVENT_TICK_STARTED,
    MaintenanceScheduler,
    MaintenanceSchedulerDeps,
    MaintenanceTickResult,
    compute_next_tick_at,
    count_current_orphans,
    count_pruned_rows,
    is_tick_due,
    render_iso,
    tick_to_dict,
    tick_within_jitter_window,
)

pytestmark = pytest.mark.unit


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _seed_orphan_vector(
    db: sqlite3.Connection,
    *,
    hash_: str = "orphan-1",
    seq: int = 0,
) -> None:
    """Insert a content_vectors row with no matching documents row."""
    db.execute(
        "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
        (hash_, seq, 0),
    )
    db.commit()


def _seed_active_document_and_vector(db: sqlite3.Connection, *, hash_: str = "doc-1") -> None:
    """Insert a matched (documents, content_vectors) pair — must NOT be pruned."""
    now = "2026-05-24T00:00:00Z"
    db.execute(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', 'doc.md', ?, NULL, NULL, NULL, NULL, 'public', ?, ?, 1)",
        (hash_, now, now),
    )
    db.execute(
        "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
        (hash_,),
    )
    db.commit()


def _deps(
    *,
    epoch: float = 0.0,
    usearch_ok: bool = True,
    fts_healed: int = 0,
    bronze_reaped: int = 0,
    bronze_ttl_deleted: int = 0,
) -> MaintenanceSchedulerDeps:
    """Build a Deps with deterministic clock + no-op usearch/fts/bronze seams."""
    return MaintenanceSchedulerDeps(
        usearch_rebuilder=lambda: usearch_ok,
        fts_healer=lambda _db: fts_healed,
        clock=lambda: epoch,
        bronze_reaper=lambda: bronze_reaped,
        bronze_ttl_gc=lambda: bronze_ttl_deleted,
    )


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_tick_prunes_orphan_into_soft_delete_table() -> None:
    """Sabotage: change the orphan SELECT to ``WHERE 1=0`` — count drops to 0."""
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))

    result = scheduler.tick(db)

    assert isinstance(result, MaintenanceTickResult)
    assert result.orphans_pruned == 1, f"expected 1 orphan pruned; got {result.orphans_pruned}"
    assert result.pruned_table_size == 1
    assert result.current_orphan_count == 0
    # The orphan row moved from content_vectors to content_vectors_pruned.
    live = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash = 'orphan-1'").fetchone()[0]
    pruned = db.execute("SELECT COUNT(*) FROM content_vectors_pruned WHERE hash = 'orphan-1'").fetchone()[0]
    assert live == 0
    assert pruned == 1


def test_tick_preserves_active_document_vectors() -> None:
    """Sabotage: change the LEFT JOIN to INNER JOIN — non-orphan also pruned."""
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    _seed_active_document_and_vector(db, hash_="doc-1")
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))

    result = scheduler.tick(db)
    assert result.orphans_pruned == 1
    # The matched (documents, content_vectors) pair is untouched.
    live_doc = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash = 'doc-1'").fetchone()[0]
    assert live_doc == 1, "non-orphan content_vectors must survive the tick"


# ---------------------------------------------------------------------------
# SABOTAGE PROOF 1 — Idempotency
# ---------------------------------------------------------------------------


def test_second_tick_is_idempotent_when_no_new_orphans() -> None:
    """Idempotency proof — primary guard: the DELETE from content_vectors.

    The DELETE pass clears each pruned row from ``content_vectors`` so
    a follow-up tick's orphan SELECT finds nothing. Sabotage: comment
    out the ``DELETE FROM content_vectors WHERE (hash, seq) IN (...)``
    statement in ``_prune_orphans`` and the second tick re-selects the
    same row, triggering the INSERT-OR-IGNORE defence (covered
    separately in ``test_insert_or_ignore_guards_against_pre_existing_pruned_row``).
    """
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))

    first = scheduler.tick(db)
    second = scheduler.tick(db)

    assert first.orphans_pruned == 1
    assert second.orphans_pruned == 0, (
        f"second tick on clean DB must be a no-op; got orphans_pruned={second.orphans_pruned}"
    )
    # The soft-delete row from the first tick is still resident (retention >0).
    assert second.pruned_table_size == 1


def test_insert_or_ignore_guards_against_pre_existing_pruned_row(caplog: pytest.LogCaptureFixture) -> None:
    """Defence-in-depth: ``INSERT OR IGNORE`` swallows the rare race where
    a content_vectors row and a content_vectors_pruned row share (hash, seq).

    Sabotage: change ``INSERT OR IGNORE INTO content_vectors_pruned``
    to ``INSERT INTO content_vectors_pruned``. The tick logs an
    ``event=maintenance_tick_failed pid=... stage=prune
    error=IntegrityError`` line and ``orphans_pruned`` stays at 0
    (loop bails mid-iteration). This test pins the no-IntegrityError
    contract.
    """
    db = _fresh_db()
    db.execute("INSERT INTO content_vectors (hash, seq, pos) VALUES ('orphan-shared', 0, 0)")
    db.execute(
        "INSERT INTO content_vectors_pruned (hash, seq, pos, pruned_at) "
        "VALUES ('orphan-shared', 0, 0, '2026-05-22T00:00:00Z')"
    )
    db.commit()

    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))
    with caplog.at_level(logging.WARNING, logger="kairix.maintenance"):
        result = scheduler.tick(db)

    assert result.orphans_pruned == 0, "with the duplicate guard, INSERT skips and orphans_pruned stays 0"
    # No stage=prune failure should have been logged — INSERT OR IGNORE
    # swallows the unique conflict silently.
    failure_lines = [r.getMessage() for r in caplog.records if "stage=prune" in r.getMessage()]
    assert not failure_lines, f"expected silent INSERT OR IGNORE; got failure logs: {failure_lines!r}"
    # The orphan was nevertheless DELETEd from content_vectors.
    live = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash = 'orphan-shared'").fetchone()[0]
    assert live == 0
    # Soft-delete table still has exactly one row (the pre-existing one).
    soft = db.execute("SELECT COUNT(*) FROM content_vectors_pruned WHERE hash = 'orphan-shared'").fetchone()[0]
    assert soft == 1


# ---------------------------------------------------------------------------
# SABOTAGE PROOF 2 — Soft-delete retention window
# ---------------------------------------------------------------------------


def test_pruned_row_survives_first_tick_within_retention_window() -> None:
    """Retention proof: a row pruned on tick N stays through tick N+1
    (within the retention window).

    Sabotage: drop the ``WHERE pruned_at < cutoff`` filter in
    ``_gc_pruned``. Without the filter, ``_gc_pruned`` hard-deletes
    every row each tick and ``second.pruned_table_size`` is 0.
    """
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    deps = _deps(epoch=1_000_000.0)
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=deps)

    first = scheduler.tick(db)
    assert first.pruned_table_size == 1

    # Tick again at the same epoch (still within retention window).
    # The pruned row MUST survive — that's the operator's recovery
    # affordance.
    second = scheduler.tick(db)
    assert second.pruned_table_size == 1, "soft-delete row must survive subsequent ticks within the retention window"


def test_pruned_row_hard_deleted_past_retention_window() -> None:
    """The GC step DOES drop rows past the retention window.

    Constructs a scenario where ``now > pruned_at + retention``:
    first tick uses epoch T; second tick uses epoch T + 8 days (>7d retention).
    """
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    eight_days_seconds = 8 * 86400

    # First tick at epoch T — soft-deletes the orphan.
    first_deps = _deps(epoch=1_000_000.0)
    MaintenanceScheduler(db, retention_days=7, scheduler_deps=first_deps).tick(db)
    pruned_after_first = db.execute("SELECT COUNT(*) FROM content_vectors_pruned").fetchone()[0]
    assert pruned_after_first == 1

    # Second tick at epoch T + 8d — past the 7d retention window.
    second_deps = _deps(epoch=1_000_000.0 + eight_days_seconds)
    second = MaintenanceScheduler(db, retention_days=7, scheduler_deps=second_deps).tick(db)

    assert second.pruned_table_size == 0, (
        f"row past retention must be hard-deleted; got pruned_table_size={second.pruned_table_size}"
    )


def test_retention_zero_immediately_hard_deletes() -> None:
    """retention_days=0 means the GC sweep takes everything on the next tick."""
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    deps = _deps(epoch=1_000_000.0)
    # retention=0 means cutoff=now; the row's pruned_at == now is NOT
    # strictly less than cutoff, so the FIRST tick preserves it (the
    # row is freshly stamped). A SECOND tick with the same clock sees
    # the row's pruned_at == cutoff (not <), so it still survives.
    # But shifting the clock forward by one second crosses the
    # boundary.
    scheduler = MaintenanceScheduler(db, retention_days=0, scheduler_deps=deps)
    first = scheduler.tick(db)
    assert first.pruned_table_size == 1

    # Bump the clock by one second; now the cutoff has moved past
    # pruned_at and GC takes the row.
    advanced_deps = _deps(epoch=1_000_001.0)
    second = MaintenanceScheduler(db, retention_days=0, scheduler_deps=advanced_deps).tick(db)
    assert second.pruned_table_size == 0


# ---------------------------------------------------------------------------
# Stage failure isolation
# ---------------------------------------------------------------------------


def test_usearch_failure_does_not_crash_tick(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage proof: usearch rebuild failure surfaces in logs, tick still returns."""
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    deps = MaintenanceSchedulerDeps(
        usearch_rebuilder=lambda: (_ for _ in ()).throw(RuntimeError("usearch boom")),
        fts_healer=lambda _db: 0,
        clock=lambda: 1_000_000.0,
    )
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=deps)

    with caplog.at_level(logging.WARNING, logger="kairix.maintenance"):
        result = scheduler.tick(db)
    assert result.usearch_rebuilt is False
    # The prune still happened — usearch failure doesn't block the
    # SQLite-side cleanup.
    assert result.orphans_pruned == 1
    assert any("stage=usearch" in m.getMessage() for m in caplog.records)


def test_fts_healer_failure_does_not_crash_tick(caplog: pytest.LogCaptureFixture) -> None:
    """Sabotage proof: FTS healer failure surfaces in logs, tick still returns."""
    db = _fresh_db()
    deps = MaintenanceSchedulerDeps(
        usearch_rebuilder=lambda: True,
        fts_healer=lambda _db: (_ for _ in ()).throw(RuntimeError("fts boom")),
        clock=lambda: 1_000_000.0,
    )
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=deps)
    with caplog.at_level(logging.WARNING, logger="kairix.maintenance"):
        result = scheduler.tick(db)
    assert result.fts_orphans_healed == 0
    assert any("stage=fts" in m.getMessage() for m in caplog.records)


# ---------------------------------------------------------------------------
# Logging contract
# ---------------------------------------------------------------------------


def test_tick_emits_started_and_completed_log_events(caplog: pytest.LogCaptureFixture) -> None:
    """Structured-log contract: both the started + completed events fire."""
    db = _fresh_db()
    _seed_orphan_vector(db, hash_="orphan-1")
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))
    with caplog.at_level(logging.INFO, logger="kairix.maintenance"):
        scheduler.tick(db)
    messages = [r.getMessage() for r in caplog.records]
    assert any(EVENT_TICK_STARTED in m for m in messages), f"missing started event: {messages!r}"
    assert any(EVENT_TICK_COMPLETED in m for m in messages), f"missing completed event: {messages!r}"
    # The completion line carries every result field for log-only consumers.
    completion = next(m for m in messages if EVENT_TICK_COMPLETED in m)
    for token in (
        "orphans_pruned=1",
        "pruned_table_size=1",
        "fts_orphans_healed=0",
        "current_orphan_count=0",
        "elapsed_ms=",
    ):
        assert token in completion, f"missing token {token!r} in completion log: {completion!r}"


# ---------------------------------------------------------------------------
# Helper API
# ---------------------------------------------------------------------------


def test_tick_to_dict_exports_every_field() -> None:
    """JSON envelope contract — used by CLI / preflight surfaces."""
    result = MaintenanceTickResult(
        orphans_pruned=3,
        pruned_table_size=12,
        usearch_rebuilt=True,
        fts_orphans_healed=2,
        current_orphan_count=0,
        elapsed_ms=42,
        bronze_orphans_reaped=7,
        bronze_ttl_gc_deleted=11,
    )
    d = tick_to_dict(result)
    assert d == {
        "orphans_pruned": 3,
        "pruned_table_size": 12,
        "usearch_rebuilt": True,
        "fts_orphans_healed": 2,
        "current_orphan_count": 0,
        "elapsed_ms": 42,
        "bronze_orphans_reaped": 7,
        "bronze_ttl_gc_deleted": 11,
    }


def test_count_current_orphans_returns_zero_on_legacy_schema() -> None:
    db = sqlite3.connect(":memory:")
    # No schema applied — count_current_orphans must not crash.
    assert count_current_orphans(db) == 0


def test_count_pruned_rows_returns_zero_on_legacy_schema() -> None:
    db = sqlite3.connect(":memory:")
    assert count_pruned_rows(db) == 0


def test_compute_next_tick_at_never_ticked() -> None:
    assert compute_next_tick_at(0.0, 3600) == 0.0


def test_compute_next_tick_at_after_tick() -> None:
    assert compute_next_tick_at(1_000_000.0, 3600) == 1_003_600.0


def test_is_tick_due_first_run() -> None:
    """``last_tick_at == 0`` → due immediately on first iteration."""
    assert is_tick_due(now=1_000_000.0, last_tick_at=0.0, interval_seconds=3600) is True


def test_is_tick_due_after_interval() -> None:
    assert is_tick_due(now=1_004_000.0, last_tick_at=1_000_000.0, interval_seconds=3600) is True


def test_is_tick_due_before_interval() -> None:
    assert is_tick_due(now=1_001_000.0, last_tick_at=1_000_000.0, interval_seconds=3600) is False


def test_jitter_window_never_ticked() -> None:
    """Never-ticked is NOT within the jitter window — operator should see failure."""
    assert tick_within_jitter_window(now=1_000_000.0, last_tick_at=0.0, interval_seconds=3600) is False


def test_jitter_window_within() -> None:
    # 4500s since last tick, interval 3600, jitter cap = 5400 → True.
    assert tick_within_jitter_window(now=1_004_500.0, last_tick_at=1_000_000.0, interval_seconds=3600) is True


def test_jitter_window_outside() -> None:
    # 6000s since last tick, interval 3600, jitter cap = 5400 → False.
    assert tick_within_jitter_window(now=1_006_000.0, last_tick_at=1_000_000.0, interval_seconds=3600) is False


def test_render_iso_handles_zero() -> None:
    assert render_iso(0.0) == ""


def test_render_iso_formats_epoch() -> None:
    rendered = render_iso(1_716_336_000.0)  # 2024-05-22T00:00:00Z
    assert rendered.endswith("Z")
    assert "2024" in rendered


# ---------------------------------------------------------------------------
# Constructor guards
# ---------------------------------------------------------------------------


def test_constructor_rejects_negative_retention_days() -> None:
    """Negative retention is operator error — raise with a fix hint."""
    db = _fresh_db()
    with pytest.raises(ValueError, match="retention_days must be >= 0"):
        MaintenanceScheduler(db, retention_days=-1)


def test_retention_days_property_exposes_value() -> None:
    db = _fresh_db()
    scheduler = MaintenanceScheduler(db, retention_days=14)
    assert scheduler.retention_days == 14


# ---------------------------------------------------------------------------
# Per-stage SQL boundary — defensive against missing tables
# ---------------------------------------------------------------------------


def test_prune_stage_failure_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    """If the prune query raises an OperationalError, the tick logs + continues."""
    db = _fresh_db()
    # Drop content_vectors so the prune SELECT raises.
    db.execute("DROP TABLE content_vectors")
    db.commit()
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))
    with caplog.at_level(logging.WARNING, logger="kairix.maintenance"):
        result = scheduler.tick(db)
    # Tick still returns a result envelope — failure is per-stage.
    assert result.orphans_pruned == 0
    assert any("stage=prune" in m.getMessage() for m in caplog.records)


def test_pid_is_stable_within_process() -> None:
    """PID emission used by structured logs — sanity check."""
    db = _fresh_db()
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))
    pid1 = scheduler._pid()
    pid2 = scheduler._pid()
    assert pid1 == pid2 > 0


def test_deps_default_factory_does_not_explode_at_construction() -> None:
    """The Deps default factory must not trigger side effects at import time."""
    deps = MaintenanceSchedulerDeps()
    # The defaults are callables; calling them is a separate boundary.
    assert callable(deps.usearch_rebuilder)
    assert callable(deps.fts_healer)
    assert callable(deps.clock)


def test_tick_handles_external_db_kwarg() -> None:
    """``tick(db)`` honours the passed connection over the constructor one."""
    persistent_db = _fresh_db()
    other_db = _fresh_db()
    _seed_orphan_vector(other_db, hash_="orphan-other")
    scheduler = MaintenanceScheduler(persistent_db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))
    result = scheduler.tick(other_db)  # NOT the constructor's db
    assert result.orphans_pruned == 1
    # persistent_db is untouched.
    assert persistent_db.execute("SELECT COUNT(*) FROM content_vectors_pruned").fetchone()[0] == 0


def test_full_tick_with_matched_and_orphan_rows() -> None:
    """End-to-end shape: matched rows preserved, orphans pruned, log emitted."""
    db = _fresh_db()
    _seed_active_document_and_vector(db, hash_="doc-survivor")
    _seed_orphan_vector(db, hash_="orphan-victim", seq=0)
    _seed_orphan_vector(db, hash_="orphan-victim-2", seq=0)
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=_deps(epoch=1_000_000.0))
    result: Any = scheduler.tick(db)
    assert result.orphans_pruned == 2
    survivor = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash = 'doc-survivor'").fetchone()[0]
    assert survivor == 1


def test_tick_runs_bronze_reaper_and_reports_count() -> None:
    """Stage 5: the injected bronze_reaper callable runs every tick and the
    returned count surfaces on MaintenanceTickResult.bronze_orphans_reaped."""
    db = _fresh_db()
    scheduler = MaintenanceScheduler(
        db,
        retention_days=7,
        scheduler_deps=_deps(epoch=1_000_000.0, bronze_reaped=42),
    )
    result = scheduler.tick(db)
    assert result.bronze_orphans_reaped == 42


def test_tick_swallows_bronze_reaper_exception() -> None:
    """A failing reaper must not poison the rest of the tick — other stages
    still produce their normal outputs and the count drops to 0."""
    db = _fresh_db()

    def _failing_reaper() -> int:
        raise RuntimeError("bronze reaper boom")

    deps = MaintenanceSchedulerDeps(
        usearch_rebuilder=lambda: True,
        fts_healer=lambda _db: 0,
        clock=lambda: 1_000_000.0,
        bronze_reaper=_failing_reaper,
    )
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=deps)
    result = scheduler.tick(db)
    assert result.bronze_orphans_reaped == 0
    # Tick still completes cleanly — the envelope reflects the no-prune state.
    assert result.orphans_pruned == 0


def test_tick_default_deps_carry_bronze_reaper() -> None:
    """The Deps default factory must wire a callable bronze_reaper so the
    production scheduler doesn't NPE when no Deps are passed."""
    deps = MaintenanceSchedulerDeps()
    assert callable(deps.bronze_reaper)


def test_tick_runs_bronze_ttl_gc_stage_and_reports_count() -> None:
    """Stage 6: the injected bronze_ttl_gc callable runs every tick and the
    returned count surfaces on MaintenanceTickResult.bronze_ttl_gc_deleted."""
    db = _fresh_db()
    scheduler = MaintenanceScheduler(
        db,
        retention_days=7,
        scheduler_deps=_deps(epoch=1_000_000.0, bronze_ttl_deleted=17),
    )
    result = scheduler.tick(db)
    assert result.bronze_ttl_gc_deleted == 17


def test_tick_swallows_bronze_ttl_gc_exception() -> None:
    """A failing TTL GC must not poison the tick — count drops to 0."""
    db = _fresh_db()

    def _failing_ttl() -> int:
        raise RuntimeError("ttl gc boom")

    deps = MaintenanceSchedulerDeps(
        usearch_rebuilder=lambda: True,
        fts_healer=lambda _db: 0,
        clock=lambda: 1_000_000.0,
        bronze_reaper=lambda: 0,
        bronze_ttl_gc=_failing_ttl,
    )
    scheduler = MaintenanceScheduler(db, retention_days=7, scheduler_deps=deps)
    result = scheduler.tick(db)
    assert result.bronze_ttl_gc_deleted == 0
    assert result.orphans_pruned == 0


def test_tick_default_deps_carry_bronze_ttl_gc() -> None:
    """Default Deps wire a callable bronze_ttl_gc — the production
    scheduler doesn't NPE when no Deps are passed."""
    deps = MaintenanceSchedulerDeps()
    assert callable(deps.bronze_ttl_gc)
