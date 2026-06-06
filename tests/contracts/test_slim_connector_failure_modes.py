"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`SlimConnector`.

One method (``retrieve_all_slim_docs``). Failure surface:

  * ``raises`` — surfaces typed exception when the source's id-listing
    endpoint fails; the prune cycle must NOT silently stage a full
    delete sweep on a transient backend error.
  * ``returns_empty`` — empty iterator when the container is empty.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.core.protocols import SlimConnector
from tests.fakes import FakeSlimConnector

pytestmark = pytest.mark.contract


class _FailingSlimConnector:
    """Inline :class:`SlimConnector` with raises-knob."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self._raises = raises

    def retrieve_all_slim_docs(self, container: Any) -> Iterator[str]:
        del container
        if self._raises is not None:
            raise self._raises
        return iter([])


def test_retrieve_all_slim_docs_raises_propagates_typed_exception() -> None:
    """A slim-listing backend failure surfaces — orchestrator must NOT
    interpret a silent empty list as "container is empty" because that
    would tombstone every document (catastrophic prune sweep).

    Sabotage proof: change ``_FailingSlimConnector.retrieve_all_slim_docs``
    to ``return iter([])`` instead of raising. Re-run: pytest.raises
    sees nothing. Restored.
    """
    conn: SlimConnector = _FailingSlimConnector(raises=RuntimeError("F68-slim-raises"))
    with pytest.raises(RuntimeError, match="F68-slim-raises"):
        list(conn.retrieve_all_slim_docs(container=object()))


def test_retrieve_all_slim_docs_returns_empty_when_container_empty() -> None:
    """Empty iterator when the container has no items — the orchestrator
    tombstones nothing.

    Sabotage proof: change ``FakeSlimConnector.retrieve_all_slim_docs``
    to ``return iter(["phantom"])`` on empty containers. Re-run: the
    ``== []`` assertion fails. Restored.
    """
    conn: SlimConnector = FakeSlimConnector()
    out = list(conn.retrieve_all_slim_docs(container=object()))
    assert out == [], f"empty container must yield []; got {out!r}"
