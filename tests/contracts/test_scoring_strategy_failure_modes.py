"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ScoringStrategy`.

One method (``score``). Failure surface:

  * ``raises`` — backend / parse failure surfaces verbatim; caller must
    not interpret silently-zero score as "no relevance".
  * ``returns_empty`` — when ``retrieved`` is empty the score is 0.0
    (sentinel for "nothing to score"), NOT a raise.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.protocols import ScoringStrategy

pytestmark = pytest.mark.contract


class _FailingScorer:
    """Inline :class:`ScoringStrategy` with raises-knob."""

    def __init__(self, *, raises: BaseException | None = None, default_score: float = 0.0) -> None:
        self._raises = raises
        self._default_score = default_score

    def score(self, retrieved: list[str], gold: list[dict[str, Any]]) -> float:
        if self._raises is not None:
            raise self._raises
        if not retrieved:
            return 0.0
        return self._default_score


def test_score_raises_propagates_typed_exception() -> None:
    """A scoring backend failure surfaces — caller must not interpret
    silent 0.0 as "no relevance" when the scorer crashed mid-evaluation.

    Sabotage proof: in ``_FailingScorer.score`` change
    ``raise self._raises`` to ``return 0.0``. Re-run: pytest.raises sees
    nothing and the test fails. Restored.
    """
    scorer: ScoringStrategy = _FailingScorer(raises=RuntimeError("F68-score-raises"))
    with pytest.raises(RuntimeError, match="F68-score-raises"):
        scorer.score(retrieved=["a.md"], gold=[{"path": "a.md"}])


def test_score_returns_empty_when_retrieved_list_empty() -> None:
    """Empty retrieved list yields 0.0 — sentinel for "nothing to
    score", NOT a raise.

    Sabotage proof: change ``_FailingScorer.score`` to
    ``return self._default_score`` unconditionally (drop the empty
    branch). Re-run: a non-zero default leaks through and the
    ``== 0.0`` assertion fails. Restored.
    """
    scorer: ScoringStrategy = _FailingScorer(default_score=0.7)
    assert scorer.score(retrieved=[], gold=[{"path": "a.md"}]) == 0.0
