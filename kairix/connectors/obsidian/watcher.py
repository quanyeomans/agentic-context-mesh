"""Watchdog-backed filesystem event source for the Obsidian connector.

Wraps :class:`watchdog.observers.Observer` so the connector can poll a
queue of file-change events between full-scan reconciliation passes.
The observer owns one background thread; :meth:`WatchdogSource.start`
and :meth:`WatchdogSource.stop` manage that thread's lifecycle.

Per F37, ``watchdog`` may only be imported under
``kairix/connectors/<name>/`` or ``kairix/core/connectors/`` — this
module is the allowed location for the Obsidian plugin.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

# watchdog's ``Observer`` is a platform-dispatching factory function
# rather than a class — it returns the OS-appropriate observer impl at
# call time. Type it via ``Any`` so callers can pass either the real
# factory or a recording substitute in tests without fighting mypy.
ObserverFactory = Callable[[], Any]

# Stable mapping from watchdog event-type strings to the create/modify/
# delete trichotomy the connector pipeline expects. ``moved`` lands as
# two events (delete src + create dest) per the standard rename
# convention.
_OPS_MAP: dict[str, Literal["created", "modified", "deleted"]] = {
    "created": "created",
    "modified": "modified",
    "deleted": "deleted",
}


@dataclass(frozen=True)
class FileChange:
    """One change observed by the watchdog handler.

    Frozen for F42 discipline so callers can stash these in sets / dicts
    and compare cheaply. ``item_id`` is the path relative to the vault
    root (Obsidian's canonical identifier for a note).
    """

    op: Literal["created", "modified", "deleted"]
    item_id: str
    observed_at: str  # ISO-8601 UTC


class QueueingHandler(FileSystemEventHandler):
    """Pushes every file event onto a thread-safe queue.

    Directory events are dropped — Obsidian's identifier model is
    file-scoped. ``moved`` events expand into delete-then-create pairs
    so downstream consumers don't need a separate rename code path.
    """

    def __init__(self, vault_root: Path, sink: queue.Queue[FileChange]) -> None:
        self._vault_root = vault_root.resolve()
        self._sink = sink

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _rel(self, abs_path: str) -> str | None:
        """Convert an absolute filesystem path to a vault-root-relative
        ``item_id``. Returns ``None`` if the path is outside the vault
        (which only happens for the brief window before the observer
        unschedules)."""
        try:
            rel = Path(abs_path).resolve().relative_to(self._vault_root)
        except ValueError:
            return None
        return rel.as_posix()

    def _emit(self, op: Literal["created", "modified", "deleted"], abs_path: str) -> None:
        item_id = self._rel(abs_path)
        if item_id is None:
            return
        self._sink.put_nowait(FileChange(op=op, item_id=item_id, observed_at=self._now_iso()))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if isinstance(event, FileCreatedEvent):
            self._emit("created", str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if isinstance(event, FileModifiedEvent):
            self._emit("modified", str(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if isinstance(event, FileDeletedEvent):
            self._emit("deleted", str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if isinstance(event, FileMovedEvent):
            # Treat a rename as a delete-then-create pair — Obsidian's
            # ``item_id`` is the relative path, so a move changes
            # identity.
            self._emit("deleted", str(event.src_path))
            self._emit("created", str(event.dest_path))


class WatchdogSource:
    """Thread-safe wrapper around a single :class:`Observer`.

    Lifecycle:

      1. ``start()`` — schedules the handler and starts the observer
         thread. Idempotent: a second call is a no-op (so the
         connector can call it lazily on every ``list_changes`` without
         juggling state).
      2. ``drain()`` — non-blocking, returns every queued change since
         the last drain. The queue is bounded only by available memory;
         a paused worker can stack up changes safely.
      3. ``stop()`` — unschedules the handler and joins the observer
         thread. Idempotent: a second call is a no-op.

    The class is its own lock; ``start``/``stop`` may be called from
    any thread.
    """

    def __init__(self, vault_root: Path, *, observer_factory: ObserverFactory = Observer) -> None:
        """Construct the source against the given vault root.

        ``observer_factory`` is a documented test seam — production
        callers leave it at the default, tests pass a recording
        substitute so we never start a real OS-level watcher under
        ``pytest``. F6-clean: the default is a real callable, not
        ``None``.
        """
        self._vault_root = vault_root.resolve()
        self._queue: queue.Queue[FileChange] = queue.Queue()
        self._observer_factory: ObserverFactory = observer_factory
        self._observer: Any | None = None
        self._lock = threading.Lock()

    @property
    def vault_root(self) -> Path:
        return self._vault_root

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._observer is not None

    def start(self) -> None:
        """Start the observer thread. Idempotent."""
        with self._lock:
            if self._observer is not None:
                return
            observer = self._observer_factory()
            handler = QueueingHandler(self._vault_root, self._queue)
            observer.schedule(handler, str(self._vault_root), recursive=True)
            observer.start()
            self._observer = observer

    def stop(self) -> None:
        """Unschedule the handler and join the observer thread. Idempotent."""
        with self._lock:
            observer = self._observer
            self._observer = None
        if observer is None:
            return
        observer.unschedule_all()
        observer.stop()
        observer.join(timeout=5.0)

    def drain(self) -> list[FileChange]:
        """Non-blocking — return every queued change since the last drain."""
        out: list[FileChange] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def __enter__(self) -> WatchdogSource:
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.stop()
