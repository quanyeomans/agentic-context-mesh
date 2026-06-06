"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`FusionStrategy`.

``FusionStrategy.fuse`` merges BM25 and vector result lists. The
SearchPipeline contract is "fuse never blocks a response" — if fusion
raises, the pipeline must surface the exception (caller can decide to
degrade or fail). When both inputs are empty the result is empty.

Fakes from :mod:`tests.fakes` are used directly — :class:`FakeFusion`
exposes a ``raises=`` knob for the canonical "raises" failure shape.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import FusionStrategy
from tests.fakes import FakeFusion

pytestmark = pytest.mark.contract


def test_fuse_raises_propagates_typed_exception() -> None:
    """A fusion strategy that fails internally raises — the
    SearchPipeline does NOT silently fall back to empty results
    (that would mask backend errors as "no hits"). Callers see the
    exception and choose how to degrade.

    Sabotage proof: change ``FakeFusion.fuse`` to ``return []``
    instead of ``raise self._raises``. Re-ran: ``pytest.raises`` sees
    nothing and the test fails. Restored.
    """
    fusion: FusionStrategy = FakeFusion(raises=RuntimeError("F68-fuse-raises"))
    with pytest.raises(RuntimeError, match="F68-fuse-raises"):
        fusion.fuse([{"id": "a"}], [{"id": "b"}])


def test_fuse_returns_empty_when_both_inputs_empty() -> None:
    """Empty BM25 + empty vector → empty fused result (not None, not
    a sentinel). Callers iterate without a null check.

    Sabotage proof: change ``FakeFusion.fuse`` to return ``None``
    on empty inputs. Re-ran: the ``== []`` assertion fails. Restored.
    """
    fusion: FusionStrategy = FakeFusion()
    assert fusion.fuse([], []) == []
