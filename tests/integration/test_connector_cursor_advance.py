"""Multi-tick cursor-advance invariants — F62 reference test.

This file is the canonical multi-tick reference test for
:class:`~kairix.core.connectors.pipeline.ConnectorPipeline`. It is
also the test that *would have* caught the v2026.5.28a1 production
incident where the pipeline wrote per-item ``modified_at`` into
``connector_cursors`` (instead of the connector's opaque
``next_cursor()`` token), forcing a full Graph resync on every
worker tick.

Three invariants pinned:

1. **Tick 1 writes the connector's ``next_cursor()`` token** — NOT
   the per-item ``modified_at``. Tests the opaque-token shape
   (SharePoint deltaLink / Slack ts / Notion last_edited_time).
2. **Tick 2 reads the stored cursor and passes it back to
   ``connector.list_changes(stored_cursor)`` — not ``None``.** Tests
   that the orchestrator round-trips the cursor between ticks.
3. **A quiet tick (zero events) still preserves the prior cursor** —
   the orchestrator must NOT clobber a real cursor with ``None``.

Sabotage proofs (each executed by the author; re-run before commit
to confirm the test catches the regression):

  In ``kairix/core/connectors/pipeline.py:_commit_and_flush``, revert
  to ``self._cursor_store.write(source_name, chunk.latest_modified_at)``.
  Re-run :func:`test_tick1_writes_connector_next_cursor_not_modified_at`
  — the assertion ``stored_cursor == "<DELTALINK>"`` fails because
  the stored value reverts to the ISO timestamp. Restore; test passes.

  In ``kairix/core/connectors/pipeline.py:_process_batch``, restore
  the ``if chunk.latest_modified_at is not None:`` guard before the
  terminal commit. Re-run
  :func:`test_quiet_tick_preserves_prior_cursor` — tick 2's cursor
  ends up as ``None`` (or unchanged from tick 1) when the connector
  reports a new cursor on the quiet tick. Restore; test passes.

Spec: ``CLAUDE.md`` F62; ``docs/architecture/fitness-functions.md``
§F62.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core import factory
from kairix.core.connectors.pipeline import ConnectorPipeline
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.integration

# Opaque token shaped like SharePoint's ``@odata.deltaLink`` — tests the
# common production case where the cursor is unrelated to ``modified_at``.
_OPAQUE_DELTALINK_TICK1 = '{"drive-1": "https://graph.microsoft.com/v1.0/drives/drive-1/root/delta?token=tick1"}'
_OPAQUE_DELTALINK_TICK2 = '{"drive-1": "https://graph.microsoft.com/v1.0/drives/drive-1/root/delta?token=tick2"}'


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "cursor_advance.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    return db


def _build_pipeline(db: sqlite3.Connection) -> ConnectorPipeline:
    return factory.build_connector_pipeline(
        db=db,
        collection="cursor-advance-test",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )


def _two_events_with_late_modified_at() -> list[ChangeEvent]:
    """Two events whose ``modified_at`` is intentionally later than the cursor token.

    If the pipeline incorrectly persists ``modified_at`` instead of
    ``next_cursor()``, the assertion at tick-1 boundary fails because
    the stored value is a 2026-05-26 ISO timestamp not the opaque
    deltaLink.
    """
    body = ("body. " * 30).encode("utf-8")
    return [
        ChangeEvent(op="modified", item_id=f"item-{i}.md", modified_at=f"2026-05-26T10:0{i}:00Z") for i in (1, 2)
    ], body


def test_tick1_writes_connector_next_cursor_not_modified_at(tmp_path: Path) -> None:
    """The stored cursor is the connector's opaque token, never the per-item modified_at."""
    db = _open_db(tmp_path)
    events, body = _two_events_with_late_modified_at()
    connector = FakeSourceConnector(
        name="cursor-test",
        events=events,
        content={ev.item_id: body for ev in events},
        cursor_token=_OPAQUE_DELTALINK_TICK1,
    )
    pipeline = _build_pipeline(db)

    pipeline.run_batch(connector, FakeExtractor())

    stored = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        ("cursor-test",),
    ).fetchone()
    assert stored is not None, "cursor was not persisted after tick 1"
    assert stored[0] == _OPAQUE_DELTALINK_TICK1, (
        f"cursor should be connector.next_cursor()={_OPAQUE_DELTALINK_TICK1!r}, "
        f"got {stored[0]!r} — likely a modified_at timestamp instead of the opaque token"
    )
    # Per-item modified_at strings must NOT be in the stored cursor.
    for ev in events:
        assert ev.modified_at not in str(stored[0])


def test_tick2_reads_stored_cursor_and_passes_to_list_changes(tmp_path: Path) -> None:
    """Tick 2 must call ``connector.list_changes(stored_cursor)`` with the tick-1 token, not None."""
    db = _open_db(tmp_path)
    events, body = _two_events_with_late_modified_at()
    connector = FakeSourceConnector(
        name="cursor-test",
        events=events,
        content={ev.item_id: body for ev in events},
        cursor_token=_OPAQUE_DELTALINK_TICK1,
    )
    pipeline = _build_pipeline(db)

    pipeline.run_batch(connector, FakeExtractor())  # tick 1

    # Tick 2: fresh batch, same connector, same DB. The orchestrator
    # must pass the stored cursor — NOT None — to list_changes.
    pipeline.run_batch(connector, FakeExtractor())  # tick 2

    assert len(connector.list_changes_calls) == 2
    assert connector.list_changes_calls[0] is None, "tick 1 should start from no cursor"
    assert connector.list_changes_calls[1] == _OPAQUE_DELTALINK_TICK1, (
        "tick 2 should read the cursor persisted in tick 1 and pass it to list_changes; "
        f"got {connector.list_changes_calls[1]!r}"
    )


def test_quiet_tick_preserves_prior_cursor(tmp_path: Path) -> None:
    """Zero-event tick after a cursor-advancing tick must not clobber the prior cursor with None."""
    db = _open_db(tmp_path)
    events, body = _two_events_with_late_modified_at()

    # Tick 1: connector reports a cursor + emits events.
    connector_t1 = FakeSourceConnector(
        name="cursor-test",
        events=events,
        content={ev.item_id: body for ev in events},
        cursor_token=_OPAQUE_DELTALINK_TICK1,
    )
    pipeline = _build_pipeline(db)
    pipeline.run_batch(connector_t1, FakeExtractor())

    cursor_after_t1 = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        ("cursor-test",),
    ).fetchone()[0]
    assert cursor_after_t1 == _OPAQUE_DELTALINK_TICK1

    # Tick 2: same source, zero events, connector returns None from
    # next_cursor() (simulates "delta unchanged"). The orchestrator
    # must NOT clobber the stored cursor with None — that would force
    # a full resync on the next tick.
    connector_t2 = FakeSourceConnector(name="cursor-test", events=[], cursor_token=None)
    pipeline.run_batch(connector_t2, FakeExtractor())

    cursor_after_t2 = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        ("cursor-test",),
    ).fetchone()
    assert cursor_after_t2 is not None, "quiet tick wiped the cursor row entirely"
    assert cursor_after_t2[0] == _OPAQUE_DELTALINK_TICK1, (
        f"quiet tick must preserve the prior cursor token when next_cursor() returns None; got {cursor_after_t2[0]!r}"
    )


def test_quiet_tick_advances_cursor_when_connector_reports_new_token(tmp_path: Path) -> None:
    """Zero-event tick where ``next_cursor()`` advances must persist the new token.

    Mirrors Microsoft Graph behaviour where ``@odata.deltaLink`` can
    advance with no changed items (server-side delta cursor moved
    forward to reflect "scanned up to here"). The orchestrator MUST
    persist the new token so the next tick continues from the
    advanced position.
    """
    db = _open_db(tmp_path)
    events, body = _two_events_with_late_modified_at()

    # Tick 1
    connector_t1 = FakeSourceConnector(
        name="cursor-test",
        events=events,
        content={ev.item_id: body for ev in events},
        cursor_token=_OPAQUE_DELTALINK_TICK1,
    )
    pipeline = _build_pipeline(db)
    pipeline.run_batch(connector_t1, FakeExtractor())

    # Tick 2: zero events but server-side cursor advanced.
    connector_t2 = FakeSourceConnector(name="cursor-test", events=[], cursor_token=_OPAQUE_DELTALINK_TICK2)
    pipeline.run_batch(connector_t2, FakeExtractor())

    cursor_after_t2 = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        ("cursor-test",),
    ).fetchone()[0]
    assert cursor_after_t2 == _OPAQUE_DELTALINK_TICK2, (
        f"tick 2 must persist the advanced cursor token even with zero events; got {cursor_after_t2!r}"
    )


def test_iso_timestamp_cursor_shape_uses_max_modified_at(tmp_path: Path) -> None:
    """For Obsidian-shaped connectors the cursor IS the max ``modified_at`` of the last drain."""
    db = _open_db(tmp_path)
    events, body = _two_events_with_late_modified_at()
    connector = FakeSourceConnector(
        name="cursor-test",
        events=events,
        content={ev.item_id: body for ev in events},
        track_modified_at=True,
    )
    pipeline = _build_pipeline(db)

    pipeline.run_batch(connector, FakeExtractor())

    stored = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        ("cursor-test",),
    ).fetchone()
    # Connector returns max modified_at = events[-1].modified_at.
    assert stored[0] == events[-1].modified_at
