"""Step definitions for connector_gmail.feature.

Drives the real :class:`kairix.connectors.gmail.GmailConnector` against
a scripted in-process GmailClient stand-in. No real network call — the
stub returns deterministic responses for History + Message calls so
the BDD scenario can pin the connector's observable behaviour without
any real Gmail roundtrip.

Per F46, this step file reaches the connector through the
:class:`GmailConnector` constructor + the public ``client=`` seam.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import (
    GmailHeader,
    GmailMessage,
    HistoryPage,
)
from kairix.core.protocols import ChangeEvent

pytestmark = pytest.mark.bdd


class _StubGmailClient:
    """In-process GmailClient stand-in for the BDD scenario.

    Returns two seeded messages with full envelope headers. The cursor
    parameter is recorded so the scenario could assert on the cursor
    threading (not asserted by the happy_path scenario, kept for
    future expansion).
    """

    def __init__(self) -> None:
        self.observed_cursors: list[str] = []
        self._last_history_id: str | None = None

    def get_profile_history_id(self) -> str:
        return "cold-tip"

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = page_token
        self.observed_cursors.append(start_history_id)
        return HistoryPage(
            message_ids=("gmail-msg-1", "gmail-msg-2"),
            next_page_token=None,
            history_id="warm-tip",
        )

    def iter_history_message_ids(self, *, start_history_id: str) -> Iterator[str]:
        page = self.list_history(start_history_id=start_history_id)
        self._last_history_id = page.history_id
        yield from page.message_ids

    def last_history_id(self) -> str | None:
        return self._last_history_id

    def get_message(self, message_id: str) -> GmailMessage:
        return GmailMessage(
            message_id=message_id,
            thread_id=f"thread-{message_id}",
            history_id="1000",
            label_ids=("INBOX",),
            headers=(
                GmailHeader(name="From", value="agent-alpha@example.com"),
                GmailHeader(name="To", value="agent-beta@example.com"),
                GmailHeader(name="Subject", value=f"Re: {message_id}"),
                GmailHeader(name="Date", value="2026-05-28T10:00:00Z"),
            ),
            body=b"Body of the email.",
            body_mime="text/plain",
            body_truncated=False,
            attachments=(),
        )

    def stats(self) -> Any:
        from kairix.connectors.gmail.client import GmailStatsSnapshot

        return GmailStatsSnapshot(requests=0, rate_limited_403_total=0, token_refreshes=0)

    def invalidate_token(self) -> None:
        return None


@dataclass
class _GmailCtx:
    """Per-scenario context — no module-level mutable state."""

    connector: GmailConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def gmail_ctx() -> _GmailCtx:
    return _GmailCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a stubbed Gmail History API that returns two new messages since the cursor"))
def _given_two_messages(gmail_ctx: _GmailCtx) -> None:
    gmail_ctx.connector = GmailConnector(
        user_email="agent-alpha@example.com",
        client=_StubGmailClient(),  # type: ignore[arg-type]  # F3 rationale: stub mirrors GmailClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam.
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator runs the gmail connector list_changes with a warm cursor")
def _when_list_changes_warm(gmail_ctx: _GmailCtx) -> None:
    assert gmail_ctx.connector is not None, "Given step must run before When"
    gmail_ctx.events = list(gmail_ctx.connector.list_changes(cursor="warm-cursor"))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("two created change events are emitted")
def _two_created_events(gmail_ctx: _GmailCtx) -> None:
    assert len(gmail_ctx.events) == 2, f"expected 2 events, got {len(gmail_ctx.events)}: {gmail_ctx.events!r}"
    for ev in gmail_ctx.events:
        assert ev.op == "created", f"expected 'created' op, got {ev.op!r} on {ev!r}"


@then("every change event carries the gmail mailbox sensitivity tier")
def _every_event_sensitivity(gmail_ctx: _GmailCtx) -> None:
    for ev in gmail_ctx.events:
        tier = ev.metadata.get("sensitivity")
        assert tier == "client-confidential", (
            f"gmail defaults to client-confidential per the connector spec; got {tier!r} on {ev.item_id!r}"
        )


@then("the first change event item_id round-trips to a gmail.google.com source link")
def _round_trip_link(gmail_ctx: _GmailCtx) -> None:
    assert gmail_ctx.connector is not None
    assert gmail_ctx.events, "expected at least one event"
    link = gmail_ctx.connector.source_link(gmail_ctx.events[0].item_id)
    assert link.startswith("https://mail.google.com/mail/"), f"unexpected source link: {link!r}"
    assert gmail_ctx.events[0].item_id in link
