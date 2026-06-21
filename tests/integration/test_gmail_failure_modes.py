"""F68 failure-injection contract tests for the Gmail connector.

F68 requires every Protocol method to be exercised against the full
failure-mode catalogue: ``raises | times_out | returns_partial |
returns_empty | unauthorized | unavailable``.

Each ``test_*`` here drives the real :class:`GmailConnector` against
a scripted GmailClient stand-in tuned to inject one failure mode.
The asserts pin the connector's observable behaviour — typed errors
propagate, partial responses leave the connector in a sane state,
empty responses surface as zero events, unauthorized responses
propagate the typed error so the runner can transition the cc_pair
to INVALID, unavailable responses leave the cursor untouched so the
next tick retries the same window.

F1 / F2 clean — no @patch, no monkeypatch.setenv. The injection
seam is the ``client=`` constructor kwarg on :class:`GmailConnector`.

Sabotage proofs are recorded in the test docstrings — the agent
mutates the production branch, confirms the test fails, restores.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import (
    GmailHeader,
    GmailMessage,
    HistoryPage,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
    InsufficientPermissionsError,
)
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_USER = "agent-alpha@example.com"


def _make_message(message_id: str, *, body: bytes = b"body") -> GmailMessage:
    return GmailMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        history_id="1000",
        label_ids=("INBOX",),
        headers=(
            GmailHeader(name="From", value="agent-beta@example.com"),
            GmailHeader(name="To", value=_USER),
            GmailHeader(name="Subject", value=f"Re: {message_id}"),
            GmailHeader(name="Date", value="2026-05-28T09:00:00Z"),
        ),
        body=body,
        body_mime="text/plain",
        body_truncated=False,
        attachments=(),
    )


class _ScriptedClient:
    """Tunable GmailClient stand-in for failure-mode injection."""

    def __init__(
        self,
        *,
        profile_history_id: str | Exception = "tip-1",
        history_page: HistoryPage | Exception | None = None,
        message_by_id: dict[str, GmailMessage | Exception] | None = None,
    ) -> None:
        self._profile = profile_history_id
        self._history_page = history_page
        self._message_by_id = message_by_id or {}
        self._last_history_id: str | None = None

    def get_profile_history_id(self) -> str:
        if isinstance(self._profile, Exception):
            raise self._profile
        return self._profile

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = (start_history_id, page_token)
        if isinstance(self._history_page, Exception):
            raise self._history_page
        if self._history_page is None:
            return HistoryPage(message_ids=(), next_page_token=None, history_id=None)
        return self._history_page

    def iter_history_message_ids(self, *, start_history_id: str) -> Iterator[str]:
        page = self.list_history(start_history_id=start_history_id)
        self._last_history_id = page.history_id
        yield from page.message_ids

    def last_history_id(self) -> str | None:
        return self._last_history_id

    def get_message(self, message_id: str) -> GmailMessage:
        result = self._message_by_id.get(message_id)
        if result is None:
            raise KeyError(f"scripted client has no message for {message_id!r}")
        if isinstance(result, Exception):
            raise result
        return result

    def stats(self) -> Any:
        from kairix.connectors.gmail.client import GmailStatsSnapshot

        return GmailStatsSnapshot(requests=0, rate_limited_403_total=0, token_refreshes=0)

    def invalidate_token(self) -> None:
        return None


# ---------------------------------------------------------------------------
# raises — propagation of a non-typed exception
# ---------------------------------------------------------------------------


def test_gmail_list_changes_raises_propagates_runtime_error() -> None:
    """A non-typed ``RuntimeError`` from the underlying client propagates
    out of :meth:`list_changes` so the worker's per-item try/except
    catches it.

    Sabotage proof: wrapping the inner loop in try/except RuntimeError
    and swallowing would flip this test to red (no exception raised).
    """
    client = _ScriptedClient(profile_history_id=RuntimeError("scripted boom"))
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(RuntimeError, match="scripted boom"):
        list(connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# times_out — TimeoutError from the underlying client
# ---------------------------------------------------------------------------


def test_gmail_list_changes_times_out_propagates_timeout_error() -> None:
    """A :class:`TimeoutError` from the client propagates so the runner
    can dead-letter and retry on the next tick.

    Sabotage proof: wrapping the call in a broad try/except Exception
    that swallows would flip this test to red.
    """
    client = _ScriptedClient(profile_history_id=TimeoutError("scripted timeout"))
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(TimeoutError):
        list(connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# returns_partial — page with some message ids that succeed and one that errors
# ---------------------------------------------------------------------------


def test_gmail_list_changes_returns_partial_messages_propagates_first_error() -> None:
    """If one of the message fetches raises mid-drain, the partial
    progress doesn't silently land — the exception propagates so the
    runner's per-item dead-letter path catches it.

    Sabotage proof: wrapping the per-message ``get_message`` call in
    try/except and continuing on error would silently lose the failed
    item; this test flipping would catch that drift.
    """
    page = HistoryPage(
        message_ids=("msg-good", "msg-bad"),
        next_page_token=None,
        history_id="final-tip",
    )
    client = _ScriptedClient(
        history_page=page,
        message_by_id={
            "msg-good": _make_message("msg-good"),
            "msg-bad": ContainerTransientError("transient hiccup", retry_after=10.0),
        },
    )
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(ContainerTransientError):
        list(connector.list_changes(cursor="warm-cursor"))


# ---------------------------------------------------------------------------
# returns_empty — page with zero events
# ---------------------------------------------------------------------------


def test_gmail_list_changes_returns_empty_history_page_emits_no_events() -> None:
    """An empty History page (no new messages since cursor) emits zero
    events. The connector still advances the cursor to the empty
    page's terminal historyId when present.

    Sabotage proof: dropping the ``self._next_cursor = ...`` line in
    :meth:`list_changes` would leave the cursor at None after an
    empty drain — re-fetching the same window on the next tick.
    """
    page = HistoryPage(
        message_ids=(),
        next_page_token=None,
        history_id="empty-page-tip",
    )
    client = _ScriptedClient(history_page=page)
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    events = list(connector.list_changes(cursor="warm-cursor"))
    assert events == [], f"empty drain must emit zero events; got {events!r}"
    # Cursor still advances to the page's historyId so the next tick
    # picks up the same horizon.
    assert connector.next_cursor() == "empty-page-tip"


# ---------------------------------------------------------------------------
# drains-but-no-advance — non-empty drain whose page carried no advancing
# historyId (the parser defensively collapses a non-string historyId to
# None). The connector must NOT echo the stale input cursor; it must return
# None so the pipeline's None-means-don't-clobber contract preserves the
# prior persisted cursor instead of re-asserting a false advance.
# ---------------------------------------------------------------------------


def test_gmail_list_changes_drains_messages_with_no_advancing_history_id_returns_none() -> None:
    """A non-empty drain whose page carried no advancing historyId returns
    ``next_cursor() is None`` — NOT the stale input cursor.

    The ``next_cursor`` Protocol contract: ``None`` means "no cursor
    advance this tick; the orchestrator MUST NOT clobber the prior
    cursor". Echoing the input cursor falsely signals a fresh advance to
    a window we already processed, which makes the next tick re-query the
    identical window and re-emit every already-processed message.

    Sabotage proof (executed): reverting the fix to
    ``self._next_cursor = self._client.last_history_id() or cursor`` makes
    this assertion read the stale ``"warm-cursor"`` and fail.
    """
    page = HistoryPage(
        message_ids=("msg-no-advance",),
        next_page_token=None,
        # historyId is None — the parser collapsed a non-string value.
        history_id=None,
    )
    client = _ScriptedClient(
        history_page=page,
        message_by_id={"msg-no-advance": _make_message("msg-no-advance")},
    )
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    events = list(connector.list_changes(cursor="warm-cursor"))
    assert len(events) == 1, f"the message must still be emitted this tick; got {events!r}"
    assert connector.next_cursor() is None, (
        "a drain with no advancing historyId must return None (don't-clobber), "
        f"not the stale input cursor; got {connector.next_cursor()!r}"
    )


# ---------------------------------------------------------------------------
# unauthorized — credential expired / scope revoked
# ---------------------------------------------------------------------------


def test_gmail_list_changes_unauthorized_propagates_credential_expired() -> None:
    """An unauthorised response from the client surfaces as
    :class:`CredentialExpiredError` so the runner can transition the
    cc_pair to INVALID for operator rotation.

    Sabotage proof: catching CredentialExpiredError in
    :meth:`list_changes` and continuing would silently keep the
    connector running with no events; this test flipping would catch
    that drift.
    """
    client = _ScriptedClient(profile_history_id=CredentialExpiredError("token expired"))
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(CredentialExpiredError):
        list(connector.list_changes(cursor=None))


def test_gmail_list_changes_unauthorized_propagates_insufficient_permissions() -> None:
    """A scope-denied response surfaces as :class:`InsufficientPermissionsError`.

    Sabotage proof: a try/except suppressing InsufficientPermissionsError
    would let the connector silently no-op forever; this test would
    fail if that suppression were introduced.
    """
    client = _ScriptedClient(profile_history_id=InsufficientPermissionsError("scope denied"))
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(InsufficientPermissionsError):
        list(connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# unavailable — Gmail backend transient
# ---------------------------------------------------------------------------


def test_gmail_list_changes_unavailable_propagates_container_transient() -> None:
    """A transient backend error surfaces as
    :class:`ContainerTransientError`; the connector cursor stays unset
    so the next tick retries the same window.

    Sabotage proof: catching ContainerTransientError and silently
    advancing the cursor would skip the dropped window forever; this
    test would catch the cursor-advance drift via the next_cursor
    assertion below.
    """
    client = _ScriptedClient(
        profile_history_id=ContainerTransientError("backend unavailable", retry_after=30.0),
    )
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(ContainerTransientError):
        list(connector.list_changes(cursor=None))
    # The cursor must remain unchanged from its prior value (None for
    # the cold-start path) so the next tick observes the same window.
    assert connector.next_cursor() is None


def test_gmail_fetch_unavailable_propagates_container_transient() -> None:
    """A transient backend error on fetch surfaces as transient too.

    Sabotage proof: wrapping the cache-miss fetch branch in try/except
    would mask the transient and return an empty RawArtefact, which
    would silently land a chunk with no text; this test would catch
    that drift.
    """
    client = _ScriptedClient(
        message_by_id={
            "msg-transient": ContainerTransientError("fetch flaky", retry_after=5.0),
        },
    )
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    with pytest.raises(ContainerTransientError):
        connector.fetch("msg-transient")


# ---------------------------------------------------------------------------
# Two-tick re-query / re-emit proof through the real ConnectorPipeline.
#
# Drives the production pipeline (factory.build_connector_pipeline → real
# CursorStore on SQLite) across two ticks. Tick 1 drains a message whose
# History page carried no advancing historyId; the connector must return
# next_cursor()=None so the pipeline preserves the prior persisted cursor
# (don't-clobber). Tick 2's page advances cleanly with no new messages —
# the already-processed message must NOT be re-emitted into a second chunk.
# ---------------------------------------------------------------------------


class _TwoTickScriptedClient:
    """Scripted GmailClient emitting a non-advancing page then an advancing one.

    Tick 1 (cold-start) seeds the cursor at the live tip. Tick 2 drains a
    single message from a page whose ``historyId`` collapsed to ``None``
    (no advance). Tick 3 returns an empty, advancing page so the next-tick
    horizon moves forward without re-emitting the already-processed
    message. The ``start_history_id`` each ``iter`` call observed is
    recorded so the test can assert the window the connector queried.
    """

    def __init__(self) -> None:
        self._iter_windows: list[str] = []
        # Pages consumed FIFO by successive iter_history_message_ids calls.
        self._pages: list[HistoryPage] = [
            # First warm tick — one message, NO advancing historyId.
            HistoryPage(message_ids=("msg-tick1",), next_page_token=None, history_id=None),
            # Second warm tick — empty, but the horizon advanced.
            HistoryPage(message_ids=(), next_page_token=None, history_id="tip-after-tick2"),
        ]
        self._last_history_id: str | None = None

    @property
    def iter_windows(self) -> list[str]:
        return list(self._iter_windows)

    def get_profile_history_id(self) -> str:
        return "tip-cold-start"

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = (start_history_id, page_token)
        return self._pages[0]

    def iter_history_message_ids(self, *, start_history_id: str) -> Iterator[str]:
        self._iter_windows.append(start_history_id)
        page = self._pages.pop(0) if self._pages else HistoryPage(message_ids=(), next_page_token=None, history_id=None)
        # Mirror the real client: only record an advancing historyId.
        self._last_history_id = page.history_id
        yield from page.message_ids

    def last_history_id(self) -> str | None:
        return self._last_history_id

    def get_message(self, message_id: str) -> GmailMessage:
        return _make_message(message_id, body=b"tick body")

    def stats(self) -> Any:
        from kairix.connectors.gmail.client import GmailStatsSnapshot

        return GmailStatsSnapshot(requests=0, rate_limited_403_total=0, token_refreshes=0)

    def invalidate_token(self) -> None:
        return None


def test_gmail_no_advance_tick_does_not_reemit_on_next_tick(tmp_path: Path) -> None:
    """A drain with no advancing historyId does not re-emit on the next tick.

    Two-tick proof through the real ConnectorPipeline:

      * Cold-start seeds the cursor at the live tip (no events).
      * Tick 1 drains ``msg-tick1`` from a page whose historyId collapsed
        to ``None``. The connector returns ``next_cursor()=None`` so the
        pipeline's None-means-don't-clobber contract preserves the prior
        cursor rather than re-asserting a false advance.
      * Tick 2 drains an empty, advancing page — ``msg-tick1`` is NOT
        re-emitted into a second chunk.

    Sabotage proof (executed): reverting the connector fix to
    ``self._next_cursor = self._client.last_history_id() or cursor`` makes
    tick 1's post-drain ``next_cursor()`` read the stale ``"tip-cold-start"``
    instead of ``None`` — the ``assert connector.next_cursor() is None``
    below fails. The emitted-once assertion pins that the don't-clobber
    path leaves the message processed exactly once across both ticks.
    """
    client = _TwoTickScriptedClient()
    connector = GmailConnector(user_email=_USER, client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    db_path = tmp_path / "gmail_two_tick.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="gmail-no-advance-two-tick",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    # Cold-start: seeds the cursor at the live tip with no events.
    pipeline.run_batch(connector, FakeExtractor())
    # Tick 1: drains msg-tick1 from a page with NO advancing historyId.
    pipeline.run_batch(connector, FakeExtractor())
    # The no-advance drain must NOT echo the input cursor — it returns
    # None so the pipeline preserved the prior persisted cursor.
    assert connector.next_cursor() is None, (
        "after a no-advance drain the connector must report None (don't-clobber), "
        f"not a false advance; got {connector.next_cursor()!r}"
    )
    # Tick 2: empty advancing page — must not re-emit msg-tick1.
    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    item_ids = [chunk.source_uri for chunk in chunks]
    msg_tick1_count = sum(1 for uri in item_ids if "msg-tick1" in uri)
    assert msg_tick1_count == 1, (
        "msg-tick1 must be processed exactly once across both ticks; "
        f"re-emit means the no-advance tick clobbered the cursor with a false advance. "
        f"got {msg_tick1_count} occurrences in {item_ids!r}"
    )
