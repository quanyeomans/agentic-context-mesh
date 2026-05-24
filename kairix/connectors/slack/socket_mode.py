"""Slack Socket Mode WebSocket lifecycle handler.

This is the proactive-failure-mode core called out in slack.md §5:
the Socket Mode push surface needs an explicit reconnect state machine
because a silent WebSocket drop is otherwise invisible. The shape
mirrors the spec's ASCII diagram:

    apps.connections.open ──→ CONNECTED ──(WS close / heartbeat gap)──→ RECONNECTING
          ▲                       │                                          │
          │                  invalid_auth                            backoff 1s→60s+jitter
          │                       ▼                                          │
          │                  (token_revoked)                          fail x N
          │                       │                                          ▼
          └──── rotate creds ─────┘                                    POLL_ONLY (Events API / history)
                                                                              │
                                                                       (recovery) ──→ reconnect

The handler exposes a narrow surface:

  * :meth:`connect` — opens the WS via ``slack_sdk.socket_mode``, drives
    the inbound event loop, and translates raw event envelopes into the
    boundary :class:`SocketModeEvent` shape the connector consumes.
  * :meth:`disconnect` — clean shutdown; idempotent.
  * :attr:`state` — one of ``CONNECTING`` / ``CONNECTED`` /
    ``RECONNECTING`` / ``POLL_ONLY`` / ``DISCONNECTED`` so the
    operator surface (``connector status``) reads a single field.
  * :attr:`reconnect_attempts_total` — counter for the observability
    surface (slack.md §3).
  * :meth:`fail_over_to_poll_only` — explicit operator-callable trip
    to ``POLL_ONLY`` so a stuck reconnect loop is escapable.

DI seams:

  * ``transport_factory`` — production wires
    ``slack_sdk.socket_mode.SocketModeClient``; tests inject an
    in-process fake so the WebSocket layer never touches the network.
  * ``sleeper`` — used for backoff between reconnect attempts; tests
    pass a no-op so the reconnect path doesn't block the suite.
  * ``rand`` — jitter source; tests pass a deterministic stand-in so
    the backoff schedule is reproducible.

F37 satisfied — ``slack_sdk.socket_mode`` may only be imported under
``kairix/connectors/slack/``.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from kairix.core.protocols import CredentialExpiredError

logger = logging.getLogger(__name__)

# Backoff envelope — exponential with jitter; capped at 60s per
# slack.md §5 ("exponential reconnect (1s→2s→…→cap 60s, jitter)").
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 60.0
# After N reconnect failures, drop to POLL_ONLY. The framework's
# `connector status` surface exposes this transition so operators see
# why realtime stopped without parsing logs.
_RECONNECT_FAIL_BUDGET = 5


class SocketModeState(str, Enum):
    """One of the five states from the slack.md §5 state diagram.

    Stringified Enum values (``"CONNECTED"`` etc.) so structured-log
    field emission can hand the value to ``json.dumps`` without a
    custom encoder.
    """

    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    POLL_ONLY = "POLL_ONLY"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True)
class SocketModeEvent:
    """One inbound event from Slack's Socket Mode WebSocket.

    Frozen per F42. ``envelope_id`` is the Slack-side ack token —
    every payload must be ack'd or Slack will redeliver it (the
    "Events API redelivery" row of slack.md §5). ``event_type`` is
    the Slack ``type`` field (``message`` / ``file_shared`` /
    ``app_uninstalled``); the connector dispatches by this field.
    """

    envelope_id: str
    event_type: str
    payload: Mapping[str, Any]


class SocketModeTransport(Protocol):
    """The narrow surface :class:`SlackSocketModeHandler` calls into.

    Production is ``slack_sdk.socket_mode.SocketModeClient`` (the real
    WebSocket). Tests provide a deterministic in-process fake that
    yields scripted events and lets the test mutate the connection
    state to drive the reconnect path.
    """

    def open(self) -> None:
        """Establish the WebSocket. Raise to trigger the reconnect path."""

    def close(self) -> None:
        """Close the WebSocket cleanly. Idempotent."""

    def iter_events(self) -> Iterator[Mapping[str, Any]]:
        """Yield one ack envelope per inbound event until the WS closes."""

    def ack(self, envelope_id: str) -> None:
        """Send the per-event acknowledgement back to Slack."""


@dataclass
class SlackSocketModeHandler:
    """Owns the Socket Mode WebSocket lifecycle for one workspace.

    Construction is cheap — no WebSocket open happens until
    :meth:`connect` is called. The handler is single-thread-safe by
    construction (one workspace == one cc_pair == one handler); the
    state field is protected by an internal lock so the operator-facing
    status read can race the WS thread without tearing.
    """

    transport_factory: Callable[[], SocketModeTransport]
    on_event: Callable[[SocketModeEvent], None]
    on_credential_expired: Callable[[], None] = lambda: None
    sleeper: Callable[[float], None] = time.sleep
    rand: Callable[[], float] = random.random
    backoff_base_seconds: float = _BACKOFF_BASE_SECONDS
    backoff_cap_seconds: float = _BACKOFF_CAP_SECONDS
    reconnect_fail_budget: int = _RECONNECT_FAIL_BUDGET
    _state: SocketModeState = field(default=SocketModeState.DISCONNECTED, init=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _transport: SocketModeTransport | None = field(default=None, init=False)
    reconnect_attempts_total: int = field(default=0, init=False)
    """Counter surfaced as ``socket_reconnects_total`` per slack.md §3."""

    @property
    def state(self) -> SocketModeState:
        """Snapshot of the current Socket Mode lifecycle state."""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: SocketModeState) -> None:
        with self._state_lock:
            self._state = new_state
        logger.info("slack.socket_mode state -> %s", new_state.value)

    def connect(self) -> None:
        """Drive the Socket Mode lifecycle until POLL_ONLY or DISCONNECTED.

        Blocks the calling thread. Production wiring runs this in a
        dedicated worker thread that the framework's runner owns;
        tests drive it inline because the in-process transport returns
        immediately.

        Reconnect contract: the handler distinguishes two failure
        shapes:

          * :class:`CredentialExpiredError` — the workspace install is
            dead (admin removed the app or token rotated out). Fires
            the ``on_credential_expired`` callback so the connector
            can transition its cc_pair to INVALID via the framework
            lifecycle, then exits to DISCONNECTED. No retries — the
            credential is gone.

          * Any other exception — transient. Bumps the reconnect
            counter, sleeps for the exponential-backoff window, and
            re-opens up to ``reconnect_fail_budget`` times. After the
            budget is exhausted the handler trips to POLL_ONLY so the
            framework's poll surface (``conversations.history``) can
            keep ingest alive while the WebSocket recovers.
        """
        self._set_state(SocketModeState.CONNECTING)
        attempt = 0
        while True:
            try:
                self._transport = self.transport_factory()
                self._transport.open()
                self._set_state(SocketModeState.CONNECTED)
                attempt = 0  # Successful open resets the budget.
                self._drain()
                # `_drain` returns when the transport closes; treat as
                # a transient WS close and re-enter the reconnect loop.
                self.reconnect_attempts_total += 1
                self._set_state(SocketModeState.RECONNECTING)
            except CredentialExpiredError:
                # Workspace install is dead — no retries make sense.
                logger.warning("slack.socket_mode workspace install rejected (app_uninstalled / token_revoked)")
                self.on_credential_expired()
                self._set_state(SocketModeState.DISCONNECTED)
                return
            except Exception:
                # Transient — bump counter and back off.
                logger.warning("slack.socket_mode transient failure on attempt %d", attempt + 1, exc_info=True)
                self.reconnect_attempts_total += 1
                self._set_state(SocketModeState.RECONNECTING)
            attempt += 1
            if attempt >= self.reconnect_fail_budget:
                self.fail_over_to_poll_only()
                return
            self.sleeper(self._backoff_for(attempt))

    def disconnect(self) -> None:
        """Idempotent clean shutdown."""
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # nosec B110: idempotent close — log + move on; F3 rationale
                logger.warning("slack.socket_mode close raised; ignoring", exc_info=True)
            self._transport = None
        self._set_state(SocketModeState.DISCONNECTED)

    def fail_over_to_poll_only(self) -> None:
        """Trip the handler to POLL_ONLY (slack.md §5 row 1 escalation)."""
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # nosec B110: shutdown during fail-over; F3 rationale
                logger.warning("slack.socket_mode close during fail-over raised; ignoring", exc_info=True)
            self._transport = None
        self._set_state(SocketModeState.POLL_ONLY)

    def _drain(self) -> None:
        """Drain inbound events from the transport into the on_event callback."""
        assert self._transport is not None
        for raw in self._transport.iter_events():
            event = _normalise_event(raw)
            if event is None:
                continue
            try:
                self._transport.ack(event.envelope_id)
            except Exception:  # nosec B110: best-effort ack; Slack will redeliver; F3 rationale
                logger.warning("slack.socket_mode ack failed for %s", event.envelope_id, exc_info=True)
            self.on_event(event)

    def _backoff_for(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped at ``backoff_cap_seconds``."""
        base: float = min(self.backoff_cap_seconds, self.backoff_base_seconds * (2 ** (attempt - 1)))
        jitter: float = float(self.rand()) * base * 0.25  # +/- 25%
        return float(base + jitter)


def _normalise_event(raw: Mapping[str, Any]) -> SocketModeEvent | None:
    """Translate a raw Socket Mode envelope into the boundary :class:`SocketModeEvent`.

    Returns ``None`` for non-event control frames (e.g. ``hello`` /
    ``disconnect`` heartbeats) so the dispatch loop skips them. Real
    Slack envelopes always carry both ``envelope_id`` and an inner
    ``payload.event.type`` field — anything else is a transport-level
    frame.
    """
    envelope_id = raw.get("envelope_id")
    payload = raw.get("payload")
    if not isinstance(envelope_id, str) or not envelope_id:
        return None
    if not isinstance(payload, Mapping):
        return None
    event = payload.get("event")
    if not isinstance(event, Mapping):
        return None
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None
    return SocketModeEvent(envelope_id=envelope_id, event_type=event_type, payload=event)
