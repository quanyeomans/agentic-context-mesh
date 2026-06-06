"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`HierarchyConnector`.

Single method: ``load_hierarchy(cc_pair_id) -> Iterator[HierarchyNode]``.

Failure shapes:

  * **returns_empty** — empty iterator when the cc_pair has no
    folders / channels / spaces (e.g. a freshly-onboarded source
    before the first sync).
  * **raises** — backend failure surfaces; the receiver tracks the
    failure and falls back to source_uri-prefix derivation.

Both shapes pinned through :class:`FakeHierarchyConnector` + an inline
``_RaisingHierarchyConnector`` for the raises shape.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from kairix.core.protocols import HierarchyConnector
from tests.fakes import FakeHierarchyConnector

pytestmark = pytest.mark.contract


class _RaisingHierarchyConnector:
    """Inline impl: every ``load_hierarchy`` call raises."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def load_hierarchy(self, cc_pair_id: int) -> Iterator:
        del cc_pair_id
        raise self._exc


def test_load_hierarchy_returns_empty_when_cc_pair_has_no_nodes() -> None:
    """A cc_pair with no hierarchy yields nothing — callers iterate
    without a null check.

    Sabotage proof: change ``FakeHierarchyConnector.load_hierarchy``
    to yield a sentinel node when ``self._nodes`` is empty. Re-ran:
    the ``== []`` assertion fails. Restored.
    """
    conn: HierarchyConnector = FakeHierarchyConnector(nodes=[])
    assert list(conn.load_hierarchy(cc_pair_id=42)) == []


def test_load_hierarchy_raises_propagates_typed_exception() -> None:
    """Backend failure surfaces — the search-layer receiver decides
    whether to degrade to source_uri-prefix derivation; the Protocol
    contract is "raise, don't silently empty".

    Sabotage proof: change ``_RaisingHierarchyConnector.load_hierarchy``
    to ``return iter([])`` instead of raising. Re-ran:
    ``pytest.raises`` sees nothing. Restored.
    """
    conn: HierarchyConnector = _RaisingHierarchyConnector(RuntimeError("F68-hierarchy-raises"))
    with pytest.raises(RuntimeError, match="F68-hierarchy-raises"):
        list(conn.load_hierarchy(cc_pair_id=1))
