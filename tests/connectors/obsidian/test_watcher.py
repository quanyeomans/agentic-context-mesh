"""Unit tests for :mod:`kairix.connectors.obsidian.watcher`.

Exercises the lifecycle (``start`` / ``stop`` / ``drain``), idempotency,
context-manager protocol, and the path-mapping logic inside the event
handler. The real watchdog Observer is never started — every test
passes a recording substitute factory through the documented test
seam, so no OS-level thread fires under pytest.

F1-clean (no monkey-patching), F6-clean (every seam has a real
callable default), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from kairix.connectors.obsidian.watcher import (
    FileChange,
    QueueingHandler,
    WatchdogSource,
)


@dataclass
class _RecordingObserver:
    """Stand-in for :class:`watchdog.observers.Observer`.

    Records every lifecycle call so the test can assert the observer
    was started + stopped + unscheduled. ``schedule`` keeps the handler
    so the test can drive synthetic events directly.
    """

    schedule_calls: list[tuple[Any, str, bool]] = field(default_factory=list)
    started: bool = False
    stopped: bool = False
    unscheduled: bool = False
    joined: bool = False
    handler: Any = None

    def schedule(self, handler: Any, path: str, *, recursive: bool) -> None:
        self.schedule_calls.append((handler, path, recursive))
        self.handler = handler

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def unschedule_all(self) -> None:
        self.unscheduled = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


def _make_factory(target: list[_RecordingObserver]) -> Callable[[], _RecordingObserver]:
    """Return a factory that appends every constructed observer to ``target``."""

    def _factory() -> _RecordingObserver:
        observer = _RecordingObserver()
        target.append(observer)
        return observer

    return _factory


# ---------------------------------------------------------------------------
# WatchdogSource lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_start_starts_observer_and_schedules_handler(tmp_path: Path) -> None:
    """``start()`` schedules the handler and starts the observer.

    Sabotage-proof: remove the ``observer.start()`` call; ``started``
    stays False and the assertion fails.
    """
    seen: list[_RecordingObserver] = []
    src = WatchdogSource(tmp_path, observer_factory=_make_factory(seen))
    src.start()
    assert len(seen) == 1
    obs = seen[0]
    assert obs.started is True
    assert obs.schedule_calls == [(obs.handler, str(tmp_path.resolve()), True)]
    assert src.is_running is True


@pytest.mark.unit
def test_start_is_idempotent(tmp_path: Path) -> None:
    """Calling ``start()`` twice constructs only one observer.

    Sabotage-proof: drop the ``if self._observer is not None: return``
    guard; the second call constructs a second observer and the
    assertion below fails.
    """
    seen: list[_RecordingObserver] = []
    src = WatchdogSource(tmp_path, observer_factory=_make_factory(seen))
    src.start()
    src.start()
    assert len(seen) == 1


@pytest.mark.unit
def test_stop_unschedules_and_joins_observer(tmp_path: Path) -> None:
    """``stop()`` unschedules, stops, and joins the observer.

    Sabotage-proof: drop the ``observer.unschedule_all()`` call; the
    ``unscheduled`` assertion fails.
    """
    seen: list[_RecordingObserver] = []
    src = WatchdogSource(tmp_path, observer_factory=_make_factory(seen))
    src.start()
    src.stop()
    obs = seen[0]
    assert obs.unscheduled is True
    assert obs.stopped is True
    assert obs.joined is True
    assert src.is_running is False


@pytest.mark.unit
def test_stop_is_idempotent_before_start(tmp_path: Path) -> None:
    """Calling ``stop()`` before ``start()`` is a no-op.

    Sabotage-proof: change ``stop`` to ``raise`` when ``_observer is
    None``; this test fails.
    """
    src = WatchdogSource(tmp_path, observer_factory=lambda: _RecordingObserver())
    src.stop()  # never started — safe.


@pytest.mark.unit
def test_context_manager_protocol_starts_then_stops(tmp_path: Path) -> None:
    """``with WatchdogSource(...)`` runs start + stop around the block.

    Sabotage-proof: drop the ``__exit__`` body; the observer's
    ``stopped`` field stays False after the ``with`` block.
    """
    seen: list[_RecordingObserver] = []
    with WatchdogSource(tmp_path, observer_factory=_make_factory(seen)) as src:
        assert src.is_running is True
    assert seen[0].stopped is True


@pytest.mark.unit
def test_vault_root_property_exposes_resolved_path(tmp_path: Path) -> None:
    """``vault_root`` exposes the resolved input path.

    Sabotage-proof: drop the ``.resolve()`` call in ``__init__``; this
    test fails when ``tmp_path`` contains a symlink-style ``/private``
    prefix on macOS.
    """
    src = WatchdogSource(tmp_path, observer_factory=lambda: _RecordingObserver())
    assert src.vault_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# drain()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_drain_returns_queued_changes_and_empties_queue(tmp_path: Path) -> None:
    """``drain()`` pops every queued event; second call returns empty.

    Sabotage-proof: change ``drain`` to copy without dequeue; the
    second-call assertion fails.
    """
    src = WatchdogSource(tmp_path, observer_factory=lambda: _RecordingObserver())
    # Push two events directly onto the internal queue (the handler is
    # otherwise driven by the observer thread, which is faked here).
    src._queue.put_nowait(FileChange(op="created", item_id="a.md", observed_at="2026-05-22T00:00:00Z"))
    src._queue.put_nowait(FileChange(op="modified", item_id="b.md", observed_at="2026-05-22T00:00:01Z"))
    first = src.drain()
    assert [(c.op, c.item_id) for c in first] == [("created", "a.md"), ("modified", "b.md")]
    second = src.drain()
    assert second == []


# ---------------------------------------------------------------------------
# QueueingHandler — file events convert to FileChange
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_created_event_pushes_relative_item_id_onto_queue(tmp_path: Path) -> None:
    """A FileCreatedEvent pushes a FileChange with vault-relative item_id.

    Sabotage-proof: drop the ``_rel`` conversion; the assertion below
    fails because the item_id contains the full absolute path.
    """
    sink: queue.Queue[FileChange] = queue.Queue()
    handler = QueueingHandler(tmp_path, sink)
    new_file = tmp_path / "note.md"
    new_file.write_text("body", encoding="utf-8")
    handler.on_created(FileCreatedEvent(str(new_file)))
    change = sink.get_nowait()
    assert change.op == "created"
    assert change.item_id == "note.md"


@pytest.mark.unit
def test_modified_event_pushes_modified_op(tmp_path: Path) -> None:
    """A FileModifiedEvent pushes a FileChange(op='modified').

    Sabotage-proof: hard-code ``op='created'`` in ``on_modified``; this
    test fails.
    """
    sink: queue.Queue[FileChange] = queue.Queue()
    handler = QueueingHandler(tmp_path, sink)
    new_file = tmp_path / "note.md"
    new_file.write_text("body", encoding="utf-8")
    handler.on_modified(FileModifiedEvent(str(new_file)))
    change = sink.get_nowait()
    assert change.op == "modified"
    assert change.item_id == "note.md"


@pytest.mark.unit
def test_deleted_event_pushes_deleted_op(tmp_path: Path) -> None:
    """A FileDeletedEvent pushes a FileChange(op='deleted').

    Sabotage-proof: drop the ``on_deleted`` override; the queue stays
    empty and the get_nowait below raises.
    """
    sink: queue.Queue[FileChange] = queue.Queue()
    handler = QueueingHandler(tmp_path, sink)
    handler.on_deleted(FileDeletedEvent(str(tmp_path / "note.md")))
    change = sink.get_nowait()
    assert change.op == "deleted"
    assert change.item_id == "note.md"


@pytest.mark.unit
def test_moved_event_expands_into_delete_then_create_pair(tmp_path: Path) -> None:
    """A FileMovedEvent surfaces as a deleted+created pair.

    Sabotage-proof: drop the second ``_emit`` call in ``on_moved``;
    only one event arrives and the length assertion fails.
    """
    sink: queue.Queue[FileChange] = queue.Queue()
    handler = QueueingHandler(tmp_path, sink)
    src = tmp_path / "old.md"
    dest = tmp_path / "new.md"
    src.write_text("body", encoding="utf-8")
    dest.write_text("body", encoding="utf-8")
    handler.on_moved(FileMovedEvent(str(src), str(dest)))
    first = sink.get_nowait()
    second = sink.get_nowait()
    assert (first.op, first.item_id) == ("deleted", "old.md")
    assert (second.op, second.item_id) == ("created", "new.md")


@pytest.mark.unit
def test_directory_events_are_dropped(tmp_path: Path) -> None:
    """Directory-level events don't surface — Obsidian's id model is file-scoped.

    Sabotage-proof: drop the ``if event.is_directory: return`` guards;
    the queue then carries directory events.
    """
    sink: queue.Queue[FileChange] = queue.Queue()
    handler = QueueingHandler(tmp_path, sink)
    sub = tmp_path / "subdir"
    sub.mkdir()
    # Watchdog emits separate ``Dir*Event`` subclasses for directory events;
    # ``is_directory`` is a class attribute, not an init kwarg.
    handler.on_created(DirCreatedEvent(str(sub)))
    handler.on_modified(DirModifiedEvent(str(sub)))
    handler.on_deleted(DirDeletedEvent(str(sub)))
    handler.on_moved(DirMovedEvent(str(sub), str(tmp_path / "other")))
    assert sink.empty(), "directory events leaked into the queue"


@pytest.mark.unit
def test_event_outside_vault_is_dropped(tmp_path: Path) -> None:
    """An event whose path resolves outside ``vault_root`` is dropped.

    Sabotage-proof: drop the ``ValueError`` guard in ``_rel``; the queue
    then carries a FileChange with the absolute outside path.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("body", encoding="utf-8")
    sink: queue.Queue[FileChange] = queue.Queue()
    handler = QueueingHandler(vault, sink)
    handler.on_created(FileCreatedEvent(str(outside)))
    assert sink.empty(), "event outside vault leaked into the queue"
