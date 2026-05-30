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

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import (
    GmailHeader,
    GmailMessage,
    HistoryPage,
)
from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
    InsufficientPermissionsError,
)

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
