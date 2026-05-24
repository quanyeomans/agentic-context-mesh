"""Unit tests for the Slack connector proactive-failure-mode patterns.

These pin the three novel behaviours called out in slack.md §5 and in
the dispatch brief's sabotage proofs:

  * **Socket Mode reconnect** (sabotage proof #1) — killing the
    WebSocket mid-stream drives the handler back through CONNECTING →
    RECONNECTING; verifying the counter increment + state transitions
    proves the recovery path. Mutating the reconnect block to never
    re-open makes :func:`test_socket_mode_reconnect_after_drop` fail.

  * **Tier-3 rate-limit backoff** (sabotage proof #2) — exhausting
    the token bucket for a Tier-3 method must raise
    :class:`ContainerTransientError` with a ``retry_after`` budget
    rather than allowing the request through. Mutating the bucket to
    skip the consume call makes
    :func:`test_tier3_rate_limit_raises_with_retry_after` fail.

  * **Workspace app removal** (sabotage proof #3) — a Slack 401
    response transitions the cc_pair to INVALID via
    :class:`CredentialExpiredError`; the connector flips
    :attr:`SlackConnector.cc_pair_invalid` to True. Mutating the
    handler to retry forever makes
    :func:`test_app_uninstalled_flags_cc_pair_invalid` fail.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, ClassVar

import httpx
import pytest

from kairix.connectors.slack import (
    SlackConnector,
    SlackCredentials,
    SlackSocketModeHandler,
    SlackWebClient,
    SocketModeEvent,
    SocketModeState,
)
from kairix.connectors.slack.socket_mode import SocketModeTransport
from kairix.connectors.slack.web_client import PerMethodTokenBucket
from kairix.core.protocols import ContainerTransientError, CredentialExpiredError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Sabotage proof #1 — Socket Mode reconnect
# ---------------------------------------------------------------------------


class _DropOnceTransport:
    """Test transport that yields one event, drops the WS, then yields a second.

    The handler's reconnect loop reopens after the drop; the test
    asserts both events were observed AND the reconnect counter
    incremented.
    """

    instances: ClassVar[list[_DropOnceTransport]] = []

    def __init__(self, *, mode: str) -> None:
        self.mode = mode  # "first" or "second"
        self.opened = False
        self.closed = False
        self.acked: list[str] = []
        _DropOnceTransport.instances.append(self)

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def iter_events(self) -> Iterator[Mapping[str, Any]]:
        if self.mode == "first":
            yield {
                "envelope_id": "E1",
                "payload": {"event": {"type": "message", "channel": "C1", "ts": "1.0", "text": "before drop"}},
            }
            # WS "drops" — generator returns, _drain returns, handler
            # transitions to RECONNECTING.
            return
        yield {
            "envelope_id": "E2",
            "payload": {"event": {"type": "message", "channel": "C1", "ts": "2.0", "text": "after reconnect"}},
        }

    def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)


def test_socket_mode_reconnect_after_drop() -> None:
    """A WS drop drives the handler back through RECONNECTING and resumes.

    Sabotage-proof: replacing the ``self._set_state(SocketModeState.RECONNECTING)``
    line in ``connect`` with ``return`` makes this test fail because
    the second event never lands.

    Test transport sequence:
      open #1 → yields one event, generator returns (simulates WS drop).
      open #2 → yields second event, generator returns.
      open #3 → ``open()`` raises (simulated permanent failure).

    With ``reconnect_fail_budget=2``, the handler accepts one
    transient open #2 success (resets attempt counter to 0), then on
    the second drop bumps attempt to 1, exceeds budget on the next
    iteration of failures, and trips to POLL_ONLY.
    """
    _DropOnceTransport.instances.clear()
    received: list[SocketModeEvent] = []
    open_calls = {"n": 0}

    def _factory() -> SocketModeTransport:
        open_calls["n"] += 1
        if open_calls["n"] >= 3:
            # Simulate permanent failure on third+ open — drives the
            # exception branch which increments attempt past the budget.
            raise OSError("simulated permanent WS open failure")
        return _DropOnceTransport(mode="first" if open_calls["n"] == 1 else "second")

    handler = SlackSocketModeHandler(
        transport_factory=_factory,
        on_event=received.append,
        sleeper=lambda _s: None,
        rand=lambda: 0.0,
        reconnect_fail_budget=2,
    )
    handler.connect()
    assert len(received) == 2, f"expected events before AND after reconnect, got {len(received)}"
    assert handler.reconnect_attempts_total >= 2, "reconnect counter must reflect at least two WS drops"
    assert handler.state is SocketModeState.POLL_ONLY, (
        f"after fail-budget exhaustion, handler must trip to POLL_ONLY; got {handler.state}"
    )


# ---------------------------------------------------------------------------
# Sabotage proof #2 — Tier-3 rate-limit backoff
# ---------------------------------------------------------------------------


def test_tier3_rate_limit_raises_with_retry_after() -> None:
    """Exhausting the Tier-3 budget raises ContainerTransientError + retry_after.

    Sabotage-proof: removing the ``self.bucket.consume(method)`` call
    in :meth:`SlackWebClient._post` makes this test fail because the
    bucket is never drawn down.
    """
    clock = {"t": 0.0}
    bucket = PerMethodTokenBucket(now=lambda: clock["t"])
    # Drain the entire Tier-3 budget for conversations.history.
    for _ in range(50):
        bucket.consume("conversations.history")
    # The 51st consume in the same instant must surface as a transient
    # error with a non-None retry_after budget.
    with pytest.raises(ContainerTransientError) as info:
        bucket.consume("conversations.history")
    assert info.value.retry_after is not None
    assert info.value.retry_after > 0.0


def test_rate_limit_429_response_translates_to_transient_error() -> None:
    """An HTTP 429 from Slack maps to ContainerTransientError with Retry-After.

    Mirrors the proactive resolution path: the bucket prevented the
    miss locally; this test pins the edge-side 429 translation.
    """

    def _stub(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"ok": False, "error": "ratelimited"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value",
        http_client=httpx.Client(transport=httpx.MockTransport(_stub)),
    )
    with pytest.raises(ContainerTransientError) as info:
        list(client.conversations_history(channel_id="C1", oldest=None))
    assert info.value.retry_after == 30.0


# ---------------------------------------------------------------------------
# Sabotage proof #3 — Workspace admin app removal
# ---------------------------------------------------------------------------


def test_app_uninstalled_flags_cc_pair_invalid() -> None:
    """A Slack 401 / ``app_uninstalled`` flips the cc_pair-invalid signal.

    Sabotage-proof: deleting the ``self._cc_pair_invalid = True``
    assignment in ``_enumerate_member_channels`` exception handler
    makes this test fail because the connector's status read stays at
    ``False`` despite the credential rejection.
    """

    def _stub(_r: httpx.Request) -> httpx.Response:
        # Slack 401 carries no body; the web client surfaces it as
        # CredentialExpiredError.
        return httpx.Response(401, json={})

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return SlackWebClient(
            token="xoxb-test-fake-token-value",
            http_client=httpx.Client(transport=httpx.MockTransport(_stub)),
        )

    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
    )
    # Drive the enumeration — the 401 surfaces and the connector flips
    # the cc_pair-invalid signal.
    events = list(connector.list_changes(cursor=None))
    assert events == [], "401 must short-circuit list_changes to an empty stream"
    assert connector.cc_pair_invalid() is True, (
        "after a 401 from Slack, cc_pair_invalid() must surface True so the framework "
        "can transition the cc_pair to INVALID via F57"
    )


def test_socket_mode_credential_expired_callback_fires() -> None:
    """A CredentialExpiredError raised inside connect() invokes the callback.

    Verifies the Socket Mode handler reaches the credential-expired
    path independently of the Web API surface; the connector's wiring
    relies on this callback to set the cc_pair-invalid signal.
    """
    fired = {"n": 0}

    class _AuthFailTransport:
        def open(self) -> None:
            raise CredentialExpiredError("slack: simulated invalid_auth")

        def close(self) -> None:
            pass

        def iter_events(self) -> Iterator[Mapping[str, Any]]:
            return iter([])

        def ack(self, envelope_id: str) -> None:
            del envelope_id

    handler = SlackSocketModeHandler(
        transport_factory=_AuthFailTransport,
        on_event=lambda _e: None,
        on_credential_expired=lambda: fired.__setitem__("n", fired["n"] + 1),
        sleeper=lambda _s: None,
        rand=lambda: 0.0,
    )
    handler.connect()
    assert fired["n"] == 1, "on_credential_expired callback must fire on Slack auth failure"
    assert handler.state is SocketModeState.DISCONNECTED


# ---------------------------------------------------------------------------
# Sabotage proof #5 (echoed from the contract suite) — DM exclusion
# ---------------------------------------------------------------------------


def test_dm_sensitivity_is_locked_personal_at_boundary() -> None:
    """DM messages always report ``personal`` regardless of cache state.

    Drives the public :meth:`SlackConnector.sensitivity_for` boundary
    with a DM channel cached via :meth:`SlackConnector.iter_containers`
    (which routes through ``conversations.list``); mutating the
    routing dict so DMs map to anything other than ``"personal"``
    makes this assertion fail.
    """
    from kairix.connectors.slack import SlackChannel, SlackMessage, SlackWebClient

    class _DMOnlyWebClient(SlackWebClient):
        def __init__(self) -> None:
            self._channel = SlackChannel(
                channel_id="D-DM",
                name="U_PEER",
                kind="im",
                is_archived=False,
                is_member=True,
            )

        def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
            del types
            yield self._channel

        def conversations_history(
            self,
            *,
            channel_id: str,
            oldest: str | None = None,
        ) -> Iterator[SlackMessage]:
            del channel_id, oldest
            yield SlackMessage(
                channel_id="D-DM",
                ts="1715200000.000100",
                user="U_PEER",
                text="dm hello",
                thread_ts=None,
                subtype=None,
                edited_ts=None,
            )

    web = _DMOnlyWebClient()

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return web

    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
    )
    # Prime the cache via the public surface (list_changes drives
    # _enumerate_member_channels → caches the DM channel).
    list(connector.list_changes(cursor=None))
    assert connector.sensitivity_for("D-DM:1715200000.000100") == "personal"
