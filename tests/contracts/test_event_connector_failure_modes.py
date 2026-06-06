"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`EventConnector`.

Four methods on the webhook-driven push surface. Each is exercised
below for at least one named failure class:

  * ``subscribe`` — Protocol allows ``None`` return when the source
    doesn't support push (the "returns_empty" shape).
  * ``renew_subscription`` — raises on a backend failure (every renew
    failure must surface so the framework can re-subscribe).
  * ``unsubscribe`` — Protocol contract is "idempotent on unknown id"
    — the empty-no-error shape.
  * ``handle_event`` — empty-iterator return when the payload carries
    no change events (the ``returns_empty`` shape callers iterate
    without a null check).

A small inline :class:`_FailingEventConnector` exposes the raises
knob the canonical :class:`FakeEventConnector` doesn't.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.core.protocols import ChangeEvent, EventConnector
from tests.fakes import FakeEventConnector

pytestmark = pytest.mark.contract


class _FailingEventConnector:
    """Inline :class:`EventConnector` with raises-knobs on every method.

    Constructor takes per-method exception sentinels — None means the
    method returns its default shape. No internal-attribute access /
    monkeypatching needed in the tests.
    """

    def __init__(
        self,
        *,
        raise_on_subscribe: BaseException | None = None,
        raise_on_renew: BaseException | None = None,
        raise_on_unsubscribe: BaseException | None = None,
        raise_on_handle_event: BaseException | None = None,
    ) -> None:
        self._raise_on_subscribe = raise_on_subscribe
        self._raise_on_renew = raise_on_renew
        self._raise_on_unsubscribe = raise_on_unsubscribe
        self._raise_on_handle_event = raise_on_handle_event

    def subscribe(self, callback_url: str) -> str | None:
        del callback_url
        if self._raise_on_subscribe is not None:
            raise self._raise_on_subscribe
        return None  # "unsupported" path

    def renew_subscription(self, subscription_id: str) -> str:
        if self._raise_on_renew is not None:
            raise self._raise_on_renew
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        del subscription_id
        if self._raise_on_unsubscribe is not None:
            raise self._raise_on_unsubscribe

    def handle_event(self, event: Any) -> Iterator[ChangeEvent]:
        del event
        if self._raise_on_handle_event is not None:
            raise self._raise_on_handle_event
        return iter([])


def test_subscribe_returns_empty_when_source_unsupported() -> None:
    """Subscribe MAY return ``None`` to signal "this connector kind
    doesn't support webhooks" — the framework falls back to polling.

    Sabotage proof: change ``_FailingEventConnector.subscribe`` to
    return ``""`` (empty string) instead of ``None``. Re-ran: the
    ``is None`` assertion fails. Restored.
    """
    conn: EventConnector = _FailingEventConnector()
    assert conn.subscribe("https://example.invalid/webhook") is None


def test_renew_subscription_raises_propagates_typed_exception() -> None:
    """Renew failure surfaces — framework needs to know so it can
    re-subscribe from scratch.

    Sabotage proof: change ``_FailingEventConnector.renew_subscription``
    to ``return ""`` instead of raising. Re-ran: ``pytest.raises`` sees
    nothing. Restored.
    """
    conn: EventConnector = _FailingEventConnector(
        raise_on_renew=RuntimeError("F68-renew-raises"),
    )
    with pytest.raises(RuntimeError, match="F68-renew-raises"):
        conn.renew_subscription("sub-1")


def test_unsubscribe_returns_empty_when_subscription_id_unknown() -> None:
    """Unsubscribe is idempotent — unknown id is a no-op, not a raise.
    Pin via the canonical :class:`FakeEventConnector` which records the
    attempted unsubscribe in ``unsubscribe_calls`` without raising.

    Sabotage proof: change ``FakeEventConnector.unsubscribe`` to raise
    on unknown ids. Re-ran: the call now raises and the test fails.
    Restored.
    """
    conn = FakeEventConnector()
    # No subscribe call beforehand — unsubscribe must absorb it.
    conn.unsubscribe("never-subscribed")
    assert conn.unsubscribe_calls == ["never-subscribed"]


def test_handle_event_returns_empty_when_payload_carries_no_changes() -> None:
    """An inbound webhook that carries no relevant changes yields
    nothing — callers iterate without a null check.

    Sabotage proof: change ``_FailingEventConnector.handle_event`` to
    return ``iter([ChangeEvent(...)])`` for empty payloads. Re-ran:
    the ``== []`` assertion fails. Restored.
    """
    conn: EventConnector = _FailingEventConnector()
    assert list(conn.handle_event({"noise": True})) == []
