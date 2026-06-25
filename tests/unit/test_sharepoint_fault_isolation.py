"""PR#4: per-drive fault isolation + 429 circuit breaker for the SharePoint connector.

F5: every behaviour is exercised through the public
``SharePointConnector.list_changes()`` surface. The breaker is observed only
via behaviour (which drives' cursors advance, whether a tick is skipped, how
many Graph calls happen) and the structured WARN logs — never by reading
private connector state.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

import httpx
import pytest

from kairix.connectors.sharepoint.connector import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
)
from kairix.connectors.sharepoint.graph_client import DriveItemRef

pytestmark = pytest.mark.unit

_CREDS = SharePointCredentials(
    tenant_id="t",
    client_id="c",
    client_secret="s",  # pragma: allowlist secret — test fixture
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://graph.microsoft.com/v1.0/drives/x/root/delta")
    return httpx.HTTPStatusError(f"{status}", request=req, response=httpx.Response(status, request=req))


def _raises(exc: Exception) -> Callable[[], Iterable[object]]:
    def _behaviour() -> Iterable[object]:
        raise exc

    return _behaviour


def _ok() -> Iterable[object]:
    return []


def _yields(items: list[DriveItemRef]) -> Callable[[], Iterable[object]]:
    return lambda: list(items)


def _yields_then_raises(items: list[DriveItemRef], exc: Exception) -> Callable[[], Iterable[object]]:
    def _behaviour() -> Iterable[object]:
        yield from items
        raise exc

    return _behaviour


def _drive_item(item_id: str, drive_id: str) -> DriveItemRef:
    return DriveItemRef(
        item_id=item_id,
        drive_id=drive_id,
        name=f"{item_id}.md",
        mime="text/markdown",
        web_url=None,
        size=1,
        last_modified_at="2026-06-25T00:00:00Z",
        removed=False,
    )


class _FakeGraph:
    """Per-drive controllable Graph stub.

    ``behaviours`` maps ``drive_id`` to a zero-arg callable that either returns
    an iterable of drive-item envelopes (success) or raises (fault). A drive
    that returned successfully reports a deltaLink; a drive that never ran (or
    raised) reports ``None``.
    """

    def __init__(self, behaviours: dict[str, Callable[[], Iterable[object]]]) -> None:
        self._behaviours = behaviours
        self._delivered: set[str] = set()
        self.drive_calls = 0

    def iter_drive_items(self, drive_id: str, start_url: str | None = None):
        self.drive_calls += 1
        result = self._behaviours.get(drive_id, _ok)()  # may raise
        self._delivered.add(drive_id)
        return iter(result)

    def last_delta_link_for_drive(self, drive_id: str) -> str | None:
        return f"delta-{drive_id}" if drive_id in self._delivered else None

    def path_exists(self, *args: object, **kwargs: object) -> bool:
        return True

    def list_drives(self, *args: object, **kwargs: object):
        return iter([])

    def resolve_site_by_path(self, *args: object, **kwargs: object):
        raise NotImplementedError

    def fetch_item_content(self, *args: object, **kwargs: object) -> bytes:
        return b""


def _connector(behaviours: dict[str, Callable[[], Iterable[object]]]) -> tuple[SharePointConnector, _FakeGraph]:
    fake = _FakeGraph(behaviours)
    connector = SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=d) for d in behaviours],
        credentials=_CREDS,
        client_builder=lambda _auth: fake,
    )
    return connector, fake


def test_one_drive_error_others_still_sync(caplog):
    """A single failing drive is skipped with a WARN; the others advance."""
    connector, _ = _connector(
        {"a": _raises(_http_error(403)), "b": _ok, "c": _ok},
    )
    with caplog.at_level("WARNING"):
        list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor is not None
    advanced = json.loads(cursor)
    assert advanced == {"b": "delta-b", "c": "delta-c"}  # a skipped, b+c advanced
    assert "event=sharepoint_drive_error" in caplog.text
    assert "drive=a" in caplog.text


def test_failed_drive_keeps_prior_cursor_for_retry():
    """A drive that raised re-persists its prior cursor so the next tick retries it."""
    prior = json.dumps({"a": "old-cursor-a", "b": "old-cursor-b"})
    connector, _ = _connector({"a": _raises(_http_error(500)), "b": _ok})
    list(connector.list_changes(cursor=prior))
    advanced = json.loads(connector.next_cursor())
    assert advanced["a"] == "old-cursor-a"  # carried forward unchanged
    assert advanced["b"] == "delta-b"  # b advanced to its new delta


def test_429_breaker_trips_then_skips_next_tick(caplog):
    """>= threshold drives exhausting 429 trips the breaker; the next tick is skipped."""
    connector, fake = _connector({d: _raises(_http_error(429)) for d in ("a", "b", "c", "d")})
    with caplog.at_level("WARNING"):
        list(connector.list_changes(cursor=None))
    assert "event=sharepoint_breaker_tripped" in caplog.text
    calls_after_trip = fake.drive_calls
    assert calls_after_trip == 4  # all four drives attempted this tick

    caplog.clear()
    with caplog.at_level("WARNING"):
        events = list(connector.list_changes(cursor=None))
    assert events == []
    assert "event=sharepoint_breaker_active" in caplog.text
    assert fake.drive_calls == calls_after_trip  # no Graph calls on the skipped tick


def test_429_breaker_skip_preserves_cursor():
    """While the breaker holds, the incoming cursor is returned unchanged."""
    connector, _ = _connector({d: _raises(_http_error(429)) for d in ("a", "b", "c")})
    list(connector.list_changes(cursor=None))  # trips (3 >= threshold)
    incoming = json.dumps({"a": "keep-a"})
    list(connector.list_changes(cursor=incoming))  # skipped tick
    assert connector.next_cursor() == incoming


def test_non_429_errors_do_not_trip_breaker(caplog):
    """403 / transient errors are isolated but never trip the 429 breaker."""
    connector, _ = _connector({d: _raises(_http_error(403)) for d in ("a", "b", "c", "d")})
    with caplog.at_level("WARNING"):
        list(connector.list_changes(cursor=None))
    assert "event=sharepoint_breaker_tripped" not in caplog.text
    # next tick proceeds (not skipped) — the drives are attempted again
    caplog.clear()
    with caplog.at_level("WARNING"):
        list(connector.list_changes(cursor=None))
    assert "event=sharepoint_breaker_active" not in caplog.text
    assert "event=sharepoint_drive_error" in caplog.text


def test_drive_failing_mid_iteration_commits_nothing():
    """A drive that yields then raises commits no partial events or cache (atomic).

    Guards the at-least-once contract: a throttle on delta page 2 must not leak
    page-1 events or poison the envelope cache; the whole drive re-drains next
    tick from its prior cursor.
    """
    connector, _ = _connector(
        {
            "a": _yields_then_raises([_drive_item("a-item", "a")], _http_error(500)),
            "b": _yields([_drive_item("b-item", "b")]),
        },
    )
    events = list(connector.list_changes(cursor=None))
    ids = {e.item_id for e in events}
    assert "a-item" not in ids  # drive a's partial item discarded
    assert "b-item" in ids  # drive b committed in full
    assert json.loads(connector.next_cursor()) == {"b": "delta-b"}
    with pytest.raises(KeyError):
        connector.fetch("a-item")  # never cached -> not fetchable


def test_429_below_threshold_does_not_trip(caplog):
    """Fewer than threshold throttled drives leaves the breaker closed."""
    connector, _ = _connector({"a": _raises(_http_error(429)), "b": _raises(_http_error(429)), "c": _ok})
    with caplog.at_level("WARNING"):
        list(connector.list_changes(cursor=None))
    assert "event=sharepoint_breaker_tripped" not in caplog.text
    assert json.loads(connector.next_cursor()) == {"c": "delta-c"}  # c still advanced
