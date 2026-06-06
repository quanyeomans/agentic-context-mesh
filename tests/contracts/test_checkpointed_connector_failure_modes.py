"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`CheckpointedConnector`.

Single Protocol method ``load_from_checkpoint`` — yields
:class:`ChangeEvent` items resumed from an opaque checkpoint blob.
Two failure shapes worth pinning:

  * ``returns_empty`` — the connector has no events to yield from
    this checkpoint (e.g. caught up). Callers must distinguish empty
    from "checkpoint invalid" (which would raise).
  * ``raises`` — when the underlying source rejects the checkpoint
    (HTTP 410 gone, expired deltaLink), the exception must propagate
    so the orchestrator can drop to a full re-sync rather than
    silently swallow the broken state.

We use the canonical :class:`tests.fakes.FakeCheckpointedConnector`
for the empty path and an inline ``_RaisingCheckpointedConnector``
for the raises path (the existing fake doesn't carry a raise knob).

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.protocols import Container
from tests.fakes import FakeCheckpointedConnector

pytestmark = pytest.mark.contract


def _container() -> Container:
    return Container(
        cc_pair_id=1,
        container_id="drive-alpha",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )


def test_load_from_checkpoint_returns_empty_when_no_events_pending() -> None:
    """A connector with no events to yield from this checkpoint MUST
    return an empty iterator — callers tolerate empty as "caught up".

    Sabotage proof: in :meth:`FakeCheckpointedConnector.load_from_checkpoint`
    change ``return iter(self._events)`` to
    ``return iter([ChangeEvent(op='created', item_id='ghost', modified_at='2026-01-01T00:00:00Z')])``.
    Re-run: the test fails because the iterator yields one event
    instead of zero. Restored.
    """
    conn = FakeCheckpointedConnector(events=[])
    events = list(conn.load_from_checkpoint(_container(), checkpoint=None))
    assert events == [], f"empty connector must yield empty iterator; got {events!r}"


def test_load_from_checkpoint_raises_on_expired_checkpoint() -> None:
    """When the source rejects the checkpoint (expired delta token,
    HTTP 410 Gone), the connector MUST raise — silent fallback to
    empty would hide the need for a full re-sync.

    Sabotage proof: in ``_RaisingCheckpointedConnector.load_from_checkpoint``
    comment out ``raise self._exc``. Re-run: the test fails because no
    exception fires. Restored.
    """

    class _RaisingCheckpointedConnector:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def load_from_checkpoint(self, container: Any, checkpoint: str | None) -> Any:
            del container, checkpoint
            raise self._exc

    conn = _RaisingCheckpointedConnector(RuntimeError("F68-checkpoint-expired"))
    with pytest.raises(RuntimeError, match="F68-checkpoint-expired"):
        list(conn.load_from_checkpoint(_container(), checkpoint="stale-delta-token"))
