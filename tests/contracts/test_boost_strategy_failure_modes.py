"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`BoostStrategy`.

``BoostStrategy.boost`` re-ranks a result list. If the boost raises,
the caller (``SearchPipeline``) must surface the exception rather than
silently returning the unboosted list — silent fallback is the
behavioural anti-pattern this Protocol replaces.

The canonical :class:`tests.fakes.FakeBoost` supports a ``raises=``
constructor kwarg that flips the boost to raise the configured
exception. We probe both the raises path and the returns_empty path
(empty input → empty output, no spurious boosting).

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeBoost

pytestmark = pytest.mark.contract


def test_boost_raises_propagates_typed_exception() -> None:
    """A boost configured with ``raises=`` must surface the exception
    type and message verbatim — no silent fallback to the unboosted
    list.

    Sabotage proof: in ``FakeBoost.boost`` change ``raise self._raises``
    to ``return results``. Re-run: the test fails because no exception
    fires and the function returns the input list. Restored.
    """
    boost = FakeBoost(raises=RuntimeError("F68-boost-raises"))
    with pytest.raises(RuntimeError, match="F68-boost-raises"):
        boost.boost(results=[{"path": "a.md"}], query="alpha", context={})


def test_boost_returns_empty_when_results_empty() -> None:
    """An empty input list MUST round-trip as empty — the boost must
    not invent entries. This is the ``returns_empty`` failure class
    (no candidates to boost, observable as empty output).

    Sabotage proof: in ``FakeBoost.boost`` change ``return results`` to
    ``return [{"path": "ghost.md"}]``. Re-run: the test fails because
    the result has one entry instead of zero. Restored.
    """
    boost = FakeBoost()
    out = boost.boost(results=[], query="anything", context={"intent": "memory"})
    assert out == [], f"empty input must yield empty output; got {out!r}"
