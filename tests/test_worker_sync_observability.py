"""SYNC-OBS — worker.py connector-sync observability (quiet ≠ dead).

The #1 root cause of the SharePoint stall was a metric blind spot: a
quiet source (polled OK, zero new docs) looked identical to a dead one
on every operator surface. These tests pin the worker-side wiring that
closes that gap:

  * ``syncs_attempted`` increments on EVERY tick (even a zero-yield one);
  * ``last_connector_tick_yielded`` is False for a quiet source, True
    when items flow;
  * the per-source structured summary line is emitted PER source PER tick;
  * the per-source "successful poll" heartbeat (``last_successful_index_time``
    + ``updated_at``) stamps on a zero-doc tick so ``cc-pair list`` shows
    the source is alive.

Test discipline mirrors ``tests/test_worker_maintenance_loop.py`` and
``tests/test_worker_connector_sync.py``: ``WorkerDeps`` /
``ConnectorSyncDeps`` injection (no monkeypatch / setenv); pure-logic
tests carry ``@pytest.mark.unit``, real-SQLite / real-connector tests
carry ``@pytest.mark.integration`` only.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors.cc_pair import create_cc_pair
from kairix.core.db.schema import create_schema
from kairix.worker import (
    ConnectorSyncDeps,
    ConnectorSyncResult,
    WorkerDeps,
    maybe_run_connector_sync_tick,
    run_connector_sync_pipeline,
)
from kairix.worker_state import WorkerPhase, WorkerState


@dataclass
class _Transitions:
    """Capture phase transitions a test triggered for assertion."""

    seen: list[WorkerPhase] = field(default_factory=list)

    def transition(self, phase: WorkerPhase) -> None:
        self.seen.append(phase)


@dataclass
class _WriteCapture:
    """Capture write_state calls (snapshot the SYNC-OBS fields)."""

    writes: list[tuple[int, bool, float]] = field(default_factory=list)

    def write(self, state: WorkerState, path: Path) -> None:
        del path
        self.writes.append((state.syncs_attempted, state.last_connector_tick_yielded, state.last_connector_sync_at))


# ---------------------------------------------------------------------------
# State-fold through the PUBLIC maybe_run_connector_sync_tick surface
# (F5: never import the private _apply_connector_sync_outcome directly).
# ---------------------------------------------------------------------------


def _run_tick_with_result(state: WorkerState, result: ConnectorSyncResult) -> None:
    """Drive one connector-sync tick with a scripted result via the public API.

    Injects a ``connector_sync_fn`` returning ``result`` so the state fold
    runs through ``maybe_run_connector_sync_tick`` (the public surface)
    rather than reaching for the private fold helper directly.
    """
    deps = WorkerDeps(connector_sync_fn=lambda: result, write_state_fn=lambda _s, _p: None)
    maybe_run_connector_sync_tick(
        deps=deps,
        transition=_Transitions().transition,
        state=state,
        state_path=Path("/tmp/unused-ws.json"),
        write_state_fn=lambda _s, _p: None,
    )


@pytest.mark.unit
def test_tick_increments_syncs_attempted_on_quiet_tick() -> None:
    """A zero-yield tick still bumps ``syncs_attempted`` and records yielded=False.

    Sabotage proof: drop the ``state.syncs_attempted += 1`` line in
    ``_apply_connector_sync_outcome`` and the first assertion fails —
    a quiet source would then leave no footprint, the exact blind spot.
    """
    state = WorkerState()
    quiet = ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0, connectors_polled=3, quiet=3)

    _run_tick_with_result(state, quiet)

    assert state.syncs_attempted == 1
    assert state.last_connector_tick_yielded is False
    assert state.last_connector_synced == 0
    assert state.last_connector_connectors_polled == 3
    assert state.last_connector_sync_at > 0


@pytest.mark.unit
def test_tick_records_yielded_true_when_items_flow() -> None:
    """A tick that synced items records yielded=True and the synced count."""
    state = WorkerState()
    active = ConnectorSyncResult(synced=5, failed=1, dead_letter_added=1, connectors_polled=2, quiet=1)

    _run_tick_with_result(state, active)

    assert state.syncs_attempted == 1
    assert state.last_connector_tick_yielded is True
    assert state.last_connector_synced == 5
    assert state.last_connector_dead_letter_added == 1


@pytest.mark.unit
def test_tick_accumulates_attempts_across_ticks() -> None:
    """``syncs_attempted`` accrues across ticks regardless of yield."""
    state = WorkerState()
    _run_tick_with_result(state, ConnectorSyncResult(synced=0, connectors_polled=1, quiet=1))
    _run_tick_with_result(state, ConnectorSyncResult(synced=2, connectors_polled=1))
    _run_tick_with_result(state, ConnectorSyncResult(synced=0, connectors_polled=1, quiet=1))

    assert state.syncs_attempted == 3
    # Last tick was quiet → the per-tick yielded flag reflects the LAST tick.
    assert state.last_connector_tick_yielded is False


# ---------------------------------------------------------------------------
# maybe_run_connector_sync_tick — runs the tick + folds + persists.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_maybe_run_tick_persists_state_on_quiet_sync() -> None:
    """A quiet sync still transitions phases, folds state, and persists once.

    Proves the worker-status surface shows the source IS being polled even
    on a zero-doc tick (``syncs_attempted`` bumped, write captured).
    """
    transitions = _Transitions()
    writes = _WriteCapture()
    state = WorkerState()

    def _quiet_sync() -> ConnectorSyncResult:
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0, connectors_polled=2, quiet=2)

    deps = WorkerDeps(connector_sync_fn=_quiet_sync, write_state_fn=writes.write)

    result = maybe_run_connector_sync_tick(
        deps=deps,
        transition=transitions.transition,
        state=state,
        state_path=Path("/tmp/unused-ws.json"),
        write_state_fn=writes.write,
    )

    assert result is not None
    assert state.syncs_attempted == 1
    assert state.last_connector_tick_yielded is False
    assert transitions.seen == [WorkerPhase.MAINTENANCE, WorkerPhase.IDLE]
    assert len(writes.writes) == 1
    captured_attempts, captured_yielded, _ = writes.writes[0]
    assert captured_attempts == 1
    assert captured_yielded is False


@pytest.mark.unit
def test_maybe_run_tick_noop_when_sync_returns_none() -> None:
    """When the sync slot is a no-op (returns None), state is untouched.

    The default ``connector_sync_fn`` raises NotImplementedError which
    ``run_connector_sync`` swallows → returns None. The tick must NOT
    fabricate a ``syncs_attempted`` bump in that case (no real poll ran).
    """
    transitions = _Transitions()
    writes = _WriteCapture()
    state = WorkerState()

    def _raise() -> ConnectorSyncResult:
        raise NotImplementedError("wave-1 default")

    deps = WorkerDeps(connector_sync_fn=_raise, write_state_fn=writes.write)

    result = maybe_run_connector_sync_tick(
        deps=deps,
        transition=transitions.transition,
        state=state,
        state_path=Path("/tmp/unused-ws.json"),
        write_state_fn=writes.write,
    )

    assert result is None
    assert state.syncs_attempted == 0
    assert writes.writes == []
    # Phases still transitioned MAINTENANCE→IDLE (control flow unchanged).
    assert transitions.seen == [WorkerPhase.MAINTENANCE, WorkerPhase.IDLE]


# ---------------------------------------------------------------------------
# Per-source structured summary line + blind-spot heartbeat (real pipeline).
# ---------------------------------------------------------------------------


def _seed_cc_pair_row(db_path: Path, *, cc_pair_name: str) -> None:
    """Pre-register a topology_cc_pairs row so the name→id lookup resolves."""
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db)
        now = "2026-06-22T00:00:00Z"
        cur = db.execute(
            "INSERT INTO topology_connectors "
            "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
            "VALUES ('obsidian', 'seed-connector', '{}', 'internal', ?, ?)",
            (now, now),
        )
        connector_id = cur.lastrowid
        assert connector_id is not None
        create_cc_pair(db, connector_id=int(connector_id), credential_id=None, name=cc_pair_name)
        # Pin a known-old updated_at + null heartbeat so the post-tick stamp is observable.
        db.execute(
            "UPDATE topology_cc_pairs SET updated_at = ?, last_successful_index_time = NULL WHERE name = ?",
            ("2000-01-01T00:00:00Z", cc_pair_name),
        )
        db.commit()
    finally:
        db.close()


def _read_heartbeat(db_path: Path, *, cc_pair_name: str) -> tuple[str | None, str]:
    db = sqlite3.connect(str(db_path))
    try:
        row = db.execute(
            "SELECT last_successful_index_time, updated_at FROM topology_cc_pairs WHERE name = ?",
            (cc_pair_name,),
        ).fetchone()
    finally:
        db.close()
    assert row is not None, f"no topology_cc_pairs row for {cc_pair_name!r}"
    return row[0], row[1]


def _obsidian_topology(vault: Path, *, cc_pair_name: str) -> dict[str, Any]:
    return {
        "topology_v2": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Obsidian Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                }
            ],
            "cc_pairs": [
                {
                    "id": "obsidian-pair",
                    "connector": "obsidian-conn",
                    "credential": None,
                    "name": cc_pair_name,
                }
            ],
        }
    }


@pytest.mark.integration
def test_sync_emits_per_source_summary_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Each connector emits a ``sync source=... attempted=1 ...`` line per tick.

    Sabotage proof: drop the ``_log_sync_source_summary`` call in
    ``_process_sync_entry`` and the per-source line vanishes — the
    assertion below fails. The line is what lets an operator see the
    source WAS reached this tick even on a zero-doc poll.
    """
    cc_pair_name = "obsidian-summary"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\nbody.\n", encoding="utf-8")

    db_path = tmp_path / "index.sqlite"
    _seed_cc_pair_row(db_path, cc_pair_name=cc_pair_name)

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _obsidian_topology(vault, cc_pair_name=cc_pair_name),
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = run_connector_sync_pipeline(deps)

    assert result.connectors_polled == 1
    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("sync source=")]
    assert summary, f"expected a per-source summary line; got {[r.getMessage() for r in caplog.records]}"
    line = summary[0]
    assert "attempted=1" in line
    assert "items_seen=" in line
    assert "cursor_advanced=" in line


@pytest.mark.integration
def test_quiet_tick_stamps_heartbeat_so_source_is_not_dead(tmp_path: Path) -> None:
    """A zero-doc poll still stamps last_successful_index_time + updated_at.

    This is the blind-spot fix: before, ``updated_at`` froze on a zero-doc
    tick so a healthy-quiet source was byte-identical on ``cc-pair list``
    to a dead one. After the second (cursor-warm, zero-new-doc) tick the
    heartbeat advances past the pinned epoch even though no docs flowed.

    Sabotage proof: remove the ``_stamp_cc_pair_last_poll`` call in
    ``_process_sync_entry`` and ``last_successful_index_time`` stays NULL
    on the quiet tick — the assertion fails. Restored, it stamps.
    """
    cc_pair_name = "obsidian-quiet"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "only.md").write_text("# Only\n\nbody.\n", encoding="utf-8")

    db_path = tmp_path / "index.sqlite"
    _seed_cc_pair_row(db_path, cc_pair_name=cc_pair_name)

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: _obsidian_topology(vault, cc_pair_name=cc_pair_name),
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    # First tick: indexes the note (active). Second tick (cursor warm, no
    # new files) surfaces zero items — the quiet case the blind spot hid.
    first = run_connector_sync_pipeline(deps)
    assert first.synced == 1
    second = run_connector_sync_pipeline(deps)
    assert second.synced == 0, f"second tick should be quiet (zero new docs); got {second}"
    assert second.connectors_polled == 1
    assert second.quiet == 1, "a polled-but-zero-item source must be counted as quiet, not dead"

    heartbeat, updated_at = _read_heartbeat(db_path, cc_pair_name=cc_pair_name)
    assert heartbeat is not None, "quiet tick must stamp last_successful_index_time (not NULL)"
    assert updated_at != "2000-01-01T00:00:00Z", "quiet tick must advance updated_at past the pinned epoch"
