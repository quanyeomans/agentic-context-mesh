"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ContradictionScorer`.

Single Protocol method ``score(claim, candidate)``. The Protocol
docstring pins the failure contract: implementations MUST NOT raise on
parse failure — return ``(0.0, "")`` instead so the composite can
aggregate cleanly. This makes ``returns_empty`` (zero score + empty
reason) the canonical failure-mode signal.

We probe :class:`CompositeContradictionScorer` with a deliberate
zero-yielding inner scorer for the empty path AND we cover the
``raises`` shape via an inline failure scorer to prove that a buggy
inner scorer's exception DOES propagate (the no-raise contract is on
parse failure, not on programming errors).

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from kairix.knowledge.contradict.scorers import CompositeContradictionScorer

pytestmark = pytest.mark.contract


def test_score_returns_empty_when_no_scorer_finds_contradiction() -> None:
    """A composite whose inner scorers all return ``(0.0, "")`` MUST
    return ``(0.0, "")`` — the empty-tuple contract proves "no
    contradiction detected" is the documented signal callers can
    distinguish from a raised exception.

    Sabotage proof: in :meth:`CompositeContradictionScorer.score` change
    the initial ``best_score = 0.0`` to ``best_score = 0.5``. Re-run:
    the test fails because the result is ``(0.5, "")`` instead of
    ``(0.0, "")``. Restored.
    """

    class _ZeroScorer:
        category = "direct"

        def score(self, claim: str, candidate: str) -> tuple[float, str]:
            del claim, candidate
            return 0.0, ""

    composite = CompositeContradictionScorer(scorers=[_ZeroScorer(), _ZeroScorer()])
    score, reason = composite.score(claim="Alpha shipped.", candidate="Alpha is shipping later.")
    assert score == 0.0, f"all-zero scorers must aggregate to 0.0; got {score}"
    assert reason == "", f"all-zero scorers must yield empty reason; got {reason!r}"


def test_score_raises_when_inner_scorer_implementation_crashes() -> None:
    """The "MUST NOT raise on parse failure" contract covers controlled
    LLM-non-compliance — a programming bug in the inner scorer (e.g.
    ``ZeroDivisionError``) MUST still surface so the operator sees the
    crash rather than getting silent zeros.

    Sabotage proof: in :meth:`CompositeContradictionScorer.score` wrap
    the ``s, r = scorer.score(...)`` call in
    ``try: ... except Exception: s, r = 0.0, ""``. Re-run: the test
    fails because the exception is swallowed and the call returns
    ``(0.0, "")``. Restored.
    """

    class _CrashingScorer:
        category = "direct"

        def score(self, claim: str, candidate: str) -> tuple[float, str]:
            del claim, candidate
            raise RuntimeError("F68-scorer-bug")

    composite = CompositeContradictionScorer(scorers=[_CrashingScorer()])
    with pytest.raises(RuntimeError, match="F68-scorer-bug"):
        composite.score(claim="a", candidate="b")
