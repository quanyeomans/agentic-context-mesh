"""``ObsidianConnector`` — SourceConnector for filesystem-backed markdown vaults.

Implements the :class:`kairix.core.protocols.SourceConnector` Protocol
for an Obsidian-style knowledge store. Two change-detection strategies
combine, exactly as KAIRIX-VISION-LANDSCAPE-AND-ROADMAP §4.4 names:

  * **Watchdog events** since the last cursor — tail a thread-safe
    queue populated by :class:`WatchdogSource`. Fires on edits the
    worker is awake to see.
  * **Full-scan reconciliation** every Nth call, or whenever the
    cursor is ``None`` — walks the vault, compares file hashes to a
    caller-supplied snapshot, emits ``created`` / ``modified`` /
    ``deleted`` events for any drift. Catches the events that fired
    while the worker was paused (process restart, suspend/resume,
    hibernation).

The cursor token is the ISO-8601 timestamp of the last successfully-
processed event. New events have ``modified_at > cursor`` per the
spec.

The connector deliberately keeps the watchdog observer running across
``list_changes`` calls — start-on-first-use, stop on
:meth:`close` / context-manager exit. Stopping and restarting between
calls would race with concurrent edits and force every reconciliation
to repopulate the queue. Process exit without an explicit ``close`` is
acceptable: the observer thread is a daemon (watchdog's default) and
dies with the process. Long-lived workers should call ``close()`` (or
use the connector as a context manager) for clean shutdown.

Per F35, this module only imports from ``kairix.connectors.obsidian.*``
(same plugin) and ``kairix.core.*`` (the Protocol surface). No reach
into other connectors, no reach into the extractor layer.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from kairix.connectors.obsidian.fs import (
    DEFAULT_MIME,
    mime_for_bytes,
    mime_for_path,
)
from kairix.connectors.obsidian.reconciler import (
    CollectionScanSpec,
    FullScanReconciler,
)
from kairix.connectors.obsidian.watcher import FileChange, WatchdogSource
from kairix.core.db.scanner import CollectionConfig
from kairix.core.protocols import (
    ChangeEvent,
    Cursor,
    RawArtefact,
    Sensitivity,
)

CONNECTOR_NAME = "obsidian"

# Default reconciliation cadence: every Nth ``list_changes`` call runs
# a full-scan pass in addition to draining the watchdog queue. Picked
# so a worker on a 10-second tick reconciles roughly every two minutes
# — short enough to catch a paused-worker miss inside one shift,
# long enough to amortise the walk on a large vault.
DEFAULT_RECONCILE_EVERY = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_known_state(_cursor: Cursor | None) -> Mapping[str, str]:
    """Production callers override this with a real document-store
    snapshot lookup. The default is empty — the connector then treats
    every file in the vault as ``created`` on first sync, which is the
    correct behaviour but slow for a large vault.

    The orchestration layer (``kairix/core/connectors/``) passes a real
    resolver against the ``documents`` table; tests pass a dict
    literal. F6-clean: the default is a real callable, not ``None``.
    """
    return {}


class ObsidianConnector:
    """SourceConnector implementation for an Obsidian markdown vault.

    Construction is cheap (no I/O, no thread started). The first
    :meth:`list_changes` call starts the watchdog observer; subsequent
    calls drain its queue. :meth:`close` stops the observer cleanly.

    DI seams:

      * ``known_state_resolver`` — production callers pass a resolver
        that queries the documents table for ``{item_id: hash}``;
        tests pass a dict literal. Default is empty mapping, which
        makes the first sync emit ``created`` for every file (correct,
        but slow).
      * ``watcher_factory`` — constructs the :class:`WatchdogSource`.
        Tests pass a factory that returns a recording stand-in so we
        never start a real OS-level watcher under pytest.
      * ``reconcile_every`` — controls how often a full scan runs on
        top of the watchdog drain.
    """

    name: str = CONNECTOR_NAME

    def __init__(
        self,
        vault_root: Path,
        collections: list[CollectionConfig] | None = None,
        sensitivity: Sensitivity = "internal",
        *,
        known_state_resolver: Callable[[Cursor | None], Mapping[str, str]] = _default_known_state,
        watcher_factory: Callable[[Path], WatchdogSource] | None = None,
        reconcile_every: int = DEFAULT_RECONCILE_EVERY,
    ) -> None:
        self._vault_root = vault_root.resolve()
        self._collections: tuple[CollectionConfig, ...] = tuple(
            collections if collections is not None else [CollectionConfig(name=CONNECTOR_NAME, path=".")]
        )
        self._sensitivity: Sensitivity = sensitivity
        self._known_state_resolver = known_state_resolver
        self._watcher_factory: Callable[[Path], WatchdogSource] = watcher_factory or WatchdogSource
        self._reconcile_every = max(1, reconcile_every)

        self._call_count = 0
        self._watcher: WatchdogSource | None = None
        self._reconciler = FullScanReconciler(
            vault_root=self._vault_root,
            collections=_to_scan_specs(self._collections),
        )

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes since ``cursor``.

        ``cursor`` is an ISO-8601 timestamp; events with
        ``modified_at <= cursor`` are filtered out. First-call
        behaviour (``cursor is None``) ALWAYS runs the reconciler so
        the worker boots into a known-good state without needing the
        watchdog to fire.
        """
        self._call_count += 1
        self._ensure_watcher_started()

        # Drain whatever the watchdog observer pushed since the last call.
        drained = self._watcher.drain() if self._watcher is not None else []
        watchdog_events = _to_change_events(drained)

        # Reconcile on cold-start AND every Nth call.
        reconcile_events: list[ChangeEvent] = []
        if cursor is None or self._call_count % self._reconcile_every == 0:
            known = self._known_state_resolver(cursor)
            reconcile_events = self._reconciler.reconcile(known)

        # De-duplicate by item_id so a watchdog "created" and a
        # reconciler "created" for the same path don't emit twice.
        # Watchdog wins when both fire — its timestamp is closer to
        # the actual edit.
        seen: set[str] = set()
        merged: list[ChangeEvent] = []
        for ev in watchdog_events + reconcile_events:
            if ev.item_id in seen:
                continue
            seen.add(ev.item_id)
            if cursor is not None and ev.modified_at <= cursor:
                continue
            merged.append(ev)
        return iter(merged)

    def fetch(self, item_id: str) -> RawArtefact:
        """Read the file at ``vault_root / item_id``.

        Mime detection is extension-first, with a magic-byte fallback
        when the extension is generic. The extractor selection
        (passthrough vs markitdown vs ocr) happens upstream in the
        pipeline, per the SC-3 separation of concerns.
        """
        abs_path = self._safe_resolve(item_id)
        raw = abs_path.read_bytes()
        mime = mime_for_path(abs_path)
        if mime == DEFAULT_MIME:
            mime = mime_for_bytes(raw, fallback=DEFAULT_MIME)
        return RawArtefact(raw=raw, mime=mime, fetched_at=_now_iso())

    def source_link(self, item_id: str) -> str:
        """``obsidian://open?vault=<vault-name>&file=<item_id>``.

        The vault name is the basename of the vault root (Obsidian's
        own convention for vault identity); ``item_id`` is the
        vault-root-relative POSIX path. Both components are URL-
        encoded so spaces and unicode pass through to the editor
        cleanly.
        """
        vault_name = self._vault_root.name
        return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(item_id, safe='/')}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector's configured sensitivity tier.

        v1 has no per-item overrides — the operator picks one tier per
        connector config block. A future ADR can add path-pattern
        overrides without breaking the Protocol.
        """
        return self._sensitivity

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop the watchdog observer thread cleanly.

        Safe to call from any thread; idempotent. Process exit without
        an explicit ``close`` works because watchdog's observer thread
        is a daemon, but long-lived workers should call this in their
        shutdown path.
        """
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def __enter__(self) -> ObsidianConnector:
        self._ensure_watcher_started()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_watcher_started(self) -> None:
        if self._watcher is not None and self._watcher.is_running:
            return
        if self._watcher is None:
            self._watcher = self._watcher_factory(self._vault_root)
        self._watcher.start()

    def _safe_resolve(self, item_id: str) -> Path:
        """Resolve ``vault_root / item_id`` and reject path traversal.

        ``item_id`` should always be a vault-root-relative POSIX
        string (the connector's own canonical form). Reject absolute
        paths and ``..`` segments that would escape the vault.
        """
        if os.path.isabs(item_id):
            raise ValueError(
                f"Obsidian item_id must be vault-relative, got absolute path {item_id!r}. "
                "fix: pass the path as emitted by list_changes (vault-root-relative POSIX). "
                "next: see kairix/connectors/obsidian/connector.py for the item_id contract."
            )
        candidate = (self._vault_root / item_id).resolve()
        try:
            candidate.relative_to(self._vault_root)
        except ValueError as exc:
            raise ValueError(
                f"Obsidian item_id {item_id!r} resolved outside vault_root {self._vault_root!s}. "
                "fix: emit item_ids only via list_changes() so they round-trip cleanly. "
                "next: investigate the upstream call that produced this id."
            ) from exc
        return candidate


def _to_scan_specs(collections: Iterable[CollectionConfig]) -> list[CollectionScanSpec]:
    """Translate scanner CollectionConfig to the reconciler's narrower spec."""
    return [CollectionScanSpec(path=c.path, glob=c.glob, exclude=tuple(c.exclude)) for c in collections]


def _to_change_events(changes: list[FileChange]) -> list[ChangeEvent]:
    """Convert watchdog drain output to the connector boundary's
    :class:`ChangeEvent` shape.
    """
    return [ChangeEvent(op=c.op, item_id=c.item_id, modified_at=c.observed_at) for c in changes]


def make_connector(config: Mapping[str, Any]) -> ObsidianConnector:
    """Construct an :class:`ObsidianConnector` from a config mapping.

    Expected keys:

      * ``vault_root`` (required) — path string or :class:`Path`.
      * ``collections`` (optional) — sequence of either
        :class:`CollectionConfig` instances OR dicts with at least
        ``name`` + ``path`` (other keys: ``glob`` / ``exclude``).
        Defaults to one collection covering the entire vault.
      * ``sensitivity`` (optional) — one of the F39 sensitivity
        literals; defaults to ``"internal"``.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``obsidian`` to this factory by name.
    """
    raw_root = config.get("vault_root")
    if raw_root is None:
        raise ValueError(
            "obsidian: config is missing 'vault_root'. "
            "fix: add vault_root: /path/to/your/vault under the obsidian connector block in kairix.config.yaml. "
            "next: see docs/architecture/connector-ingestion-architecture.md §8 for the config shape."
        )
    vault_root = Path(raw_root).expanduser()

    raw_collections = config.get("collections")
    collections: list[CollectionConfig] | None
    if raw_collections is None:
        collections = None
    else:
        collections = [_collection_from(item) for item in raw_collections]

    sensitivity: Sensitivity = config.get("sensitivity", "internal")
    return ObsidianConnector(
        vault_root=vault_root,
        collections=collections,
        sensitivity=sensitivity,
    )


def _collection_from(item: CollectionConfig | Mapping[str, Any]) -> CollectionConfig:
    """Accept either a CollectionConfig or a config-mapping shape."""
    if isinstance(item, CollectionConfig):
        return item
    name = item.get("name")
    path = item.get("path")
    if not isinstance(name, str) or not isinstance(path, str):
        raise ValueError(
            "obsidian: collection entry must declare 'name' and 'path' strings. "
            "fix: each collection block needs name + path; optional glob + exclude. "
            "next: see kairix/core/db/scanner.py:CollectionConfig for the dataclass shape."
        )
    glob = item.get("glob", "**/*.md")
    exclude = list(item.get("exclude", []))
    return CollectionConfig(name=name, path=path, glob=glob, exclude=exclude)
