"""Step definitions for feature_flag_topology_v2_gmail.feature.

Drives the real :class:`kairix.connectors.gmail.GmailConnector` with
the ``topology_v2_gmail`` flag pinned through
:class:`tests.fakes.FakeFeatureFlagResolver`. No ``@patch``, no
``monkeypatch.setattr`` on kairix internals, no ``KAIRIX_FEATURE_*``
env vars.

The string literal ``"topology_v2_gmail"`` appears verbatim in every
``with_flag(...)`` call so the F54 both-branch grep picks it up.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import (
    GmailHeader,
    GmailMessage,
    HistoryPage,
)
from kairix.core.protocols import Container
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_USER = "agent-alpha@example.com"


class _StubClient:
    """Tiny GmailClient stand-in for the BDD scenario."""

    def __init__(self) -> None:
        self._last: str | None = None

    def get_profile_history_id(self) -> str:
        return "cold-tip"

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = (start_history_id, page_token)
        return HistoryPage(
            message_ids=("gmail-msg-bdd",),
            next_page_token=None,
            history_id="warm-tip",
        )

    def iter_history_message_ids(self, *, start_history_id: str) -> Iterator[str]:
        page = self.list_history(start_history_id=start_history_id)
        self._last = page.history_id
        yield from page.message_ids

    def last_history_id(self) -> str | None:
        return self._last

    def get_message(self, message_id: str) -> GmailMessage:
        return GmailMessage(
            message_id=message_id,
            thread_id=f"thread-{message_id}",
            history_id="100",
            label_ids=("INBOX",),
            headers=(
                GmailHeader(name="From", value="agent-beta@example.com"),
                GmailHeader(name="To", value=_USER),
                GmailHeader(name="Subject", value="bdd"),
                GmailHeader(name="Date", value="2026-05-28T10:00:00Z"),
            ),
            body=b"body",
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
class _FlagCtx:
    connector: GmailConnector | None = None
    flag_value: bool | None = None


@pytest.fixture
def gmail_flag_ctx() -> _FlagCtx:
    return _FlagCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a gmail connector wired to a stubbed gmail History API"))
def _given_connector(gmail_flag_ctx: _FlagCtx) -> None:
    # The connector is built once the flag value is known so the
    # FakeFeatureFlagResolver can be wired in. Defer to the next Given.
    gmail_flag_ctx._client_stub = _StubClient()  # type: ignore[attr-defined]  # F3 rationale: scenario context attribute for the stubbed client.


@given(parsers.parse("the operator has the topology-v2-gmail flag set to {value}"))
def _given_flag_value(gmail_flag_ctx: _FlagCtx, value: str) -> None:
    parsed = value.strip().lower() == "true"
    gmail_flag_ctx.flag_value = parsed
    if parsed:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_gmail", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_gmail", False)
    stub = getattr(gmail_flag_ctx, "_client_stub", None) or _StubClient()
    gmail_flag_ctx.connector = GmailConnector(
        user_email=_USER,
        client=stub,  # type: ignore[arg-type]  # F3 rationale: stub mirrors GmailClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam.
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls list_changes_for_container on the gmail connector")
def _when_list_changes_for_container(gmail_flag_ctx: _FlagCtx) -> None:
    assert gmail_flag_ctx.connector is not None, "Given steps must run before When"
    container = Container(
        cc_pair_id=1,
        container_id=_USER,
        access_state="ACCESSIBLE",
        cursor_token="warm-cursor",
        last_synced_at=None,
    )
    list(gmail_flag_ctx.connector.list_changes_for_container(container))


# ---------------------------------------------------------------------------
# Thens — OFF branch
# ---------------------------------------------------------------------------


@then("the legacy single-cursor gmail list_changes branch is observed")
def _legacy_branch_observed(gmail_flag_ctx: _FlagCtx) -> None:
    assert gmail_flag_ctx.connector is not None
    assert gmail_flag_ctx.connector.next_cursor() is not None, (
        "OFF branch must populate the legacy connector-wide next_cursor"
    )


@then("the gmail per-container cursor map remains empty")
def _per_container_empty(gmail_flag_ctx: _FlagCtx) -> None:
    assert gmail_flag_ctx.connector is not None
    assert gmail_flag_ctx.connector.next_cursor_for_container(_USER) is None, (
        "OFF branch must NOT populate the per-container cursor map"
    )


# ---------------------------------------------------------------------------
# Thens — ON branch
# ---------------------------------------------------------------------------


@then("the gmail per-container cursor map carries one entry for the mailbox")
def _per_container_one_entry(gmail_flag_ctx: _FlagCtx) -> None:
    assert gmail_flag_ctx.connector is not None
    cursor = gmail_flag_ctx.connector.next_cursor_for_container(_USER)
    assert cursor is not None, "ON branch must populate the per-container cursor map for the mailbox"


@then("the legacy connector-wide gmail next_cursor remains unset")
def _legacy_unset(gmail_flag_ctx: _FlagCtx) -> None:
    assert gmail_flag_ctx.connector is not None
    assert gmail_flag_ctx.connector.next_cursor() is None, (
        "ON branch must NOT write the legacy connector-wide next_cursor"
    )
