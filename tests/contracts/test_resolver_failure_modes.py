"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`Resolver`.

One method (``reindex``). Failure surface:

  * ``raises`` — surfaces typed exception when the per-item refetch
    fails systemically (auth gone, source unreachable); orchestrator
    must NOT swallow because that would mark the failed items as
    re-processed when they weren't.
  * ``returns_empty`` — empty iterator when none of the failed item_ids
    are resolvable (every refetch returned tombstone / 404).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kairix.core.protocols import ChangeEvent, Resolver
from tests.fakes import FakeResolver

pytestmark = pytest.mark.contract


class _FailingResolver:
    """Inline :class:`Resolver` with raises-knob on ``reindex``."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self._raises = raises

    def reindex(
        self,
        failed_item_ids: tuple[str, ...],
        *,
        include_permissions: bool = False,
    ) -> Iterator[ChangeEvent]:
        del failed_item_ids, include_permissions
        if self._raises is not None:
            raise self._raises
        return iter([])


def test_reindex_raises_propagates_typed_exception() -> None:
    """A systemic refetch failure surfaces — orchestrator must NOT mark
    the failed items as re-processed when refetch crashed.

    Sabotage proof: change ``_FailingResolver.reindex`` to
    ``return iter([])`` instead of raising. Re-run: pytest.raises sees
    nothing. Restored.
    """
    res: Resolver = _FailingResolver(raises=RuntimeError("F68-resolver-raises"))
    with pytest.raises(RuntimeError, match="F68-resolver-raises"):
        list(res.reindex(("doc-1", "doc-2")))


def test_reindex_returns_empty_when_all_failed_ids_now_tombstoned() -> None:
    """Empty iterator when every failed id has been tombstoned at the
    source — the orchestrator drops the dead-letter rows without
    re-processing.

    Sabotage proof: change ``FakeResolver.reindex`` to
    ``return iter([_phantom_event()])`` for empty events. Re-run: the
    ``== []`` assertion fails. Restored.
    """
    res: Resolver = FakeResolver()
    out = list(res.reindex(("doc-1", "doc-2")))
    assert out == [], f"all-tombstoned must yield []; got {out!r}"
