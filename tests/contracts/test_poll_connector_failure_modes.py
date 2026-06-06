"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`PollConnector`.

One method (``list_changes_for_container``). Failure surface:

  * ``raises`` — surfaces a typed exception when the source backend
    (Graph delta endpoint, CRM API, …) raises mid-iteration; the
    orchestrator must NOT silently fall back to "no changes" because
    that would lose data.
  * ``returns_empty`` — empty iterator when no changes since the
    container's cursor; callers iterate without a None check.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.core.protocols import ChangeEvent, PollConnector
from tests.fakes import FakePollConnector

pytestmark = pytest.mark.contract


class _FailingPollConnector:
    """Inline :class:`PollConnector` with raises-knob on the iterator."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self._raises = raises

    def list_changes_for_container(self, container: Any) -> Iterator[ChangeEvent]:
        del container
        if self._raises is not None:
            raise self._raises
        return iter([])


def test_list_changes_for_container_raises_propagates_typed_exception() -> None:
    """A delta-poll backend failure surfaces — orchestrator must NOT
    interpret a silent empty iterator as "no changes" when the source
    actually crashed (that would skip a sync window and lose data).

    Sabotage proof: in ``_FailingPollConnector.list_changes_for_container``
    change ``raise self._raises`` to ``return iter([])``. Re-run:
    pytest.raises sees nothing. Restored.
    """
    conn: PollConnector = _FailingPollConnector(raises=RuntimeError("F68-poll-raises"))
    with pytest.raises(RuntimeError, match="F68-poll-raises"):
        # Realise the iterator — Protocol methods that yield can defer
        # raising until iteration; the test pins both shapes by calling
        # then iterating.
        list(conn.list_changes_for_container(container=object()))


def test_list_changes_for_container_returns_empty_when_no_changes_since_cursor() -> None:
    """Empty iterator when the cursor is already at HEAD — callers
    iterate without a None check.

    Sabotage proof: in ``FakePollConnector.list_changes_for_container``
    change ``return iter(self._events)`` to
    ``return iter([_phantom_event()])``. Re-run: the ``== []``
    assertion fails because a phantom event leaks through. Restored.
    """
    conn: PollConnector = FakePollConnector()
    out = list(conn.list_changes_for_container(container=object()))
    assert out == [], f"empty events must yield []; got {out!r}"
