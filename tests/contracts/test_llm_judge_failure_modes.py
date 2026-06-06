"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`LLMJudge`.

Two methods on the relevance-judge surface (``grade``, ``calibrate``).

Per the Protocol docstring, production implementations promise "never
raise — return all-zero grades on any error", AND ``calibrate`` returns
True/False to gate downstream eval reads. Failure surface:

  * ``grade`` — returns_empty when ``candidates`` is empty (no documents
    to grade); the grades dict is empty.
  * ``grade`` — returns_partial when the configured grades map omits
    some candidates: those default to 0 (the "never raise" contract).
  * ``calibrate`` — returns_empty when configured to fail
    (``calibration_passed=False``); downstream callers gate on the bool.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeLLMJudge

pytestmark = pytest.mark.contract


def test_grade_returns_empty_when_candidates_list_empty() -> None:
    """Empty candidates list yields an empty grades dict — the judge
    must not invent scores for non-existent documents.

    Sabotage proof: in ``FakeLLMJudge.grade`` change the dict
    comprehension to ``{"phantom-stem": 0}``. Re-run: the ``== {}``
    assertion fails because a phantom entry appears. Restored.
    """
    judge = FakeLLMJudge()
    result = judge.grade("any query", [])
    assert result.grades == {}, f"empty candidates must yield empty grades; got {result.grades!r}"


def test_grade_returns_partial_when_configured_grades_omit_some_candidates() -> None:
    """The "never raise — return zeros on error" contract: candidates
    not in the configured grades map get 0, not an exception.

    Sabotage proof: in ``FakeLLMJudge.grade`` change the dict
    comprehension to raise KeyError on missing stems. Re-run: the
    expected dict assertion fails because the call raises. Restored.
    """
    judge = FakeLLMJudge(grades_by_query={"q1": {"known-stem": 2}})
    result = judge.grade("q1", [("known-stem", "body1"), ("unknown-stem", "body2")])
    # Configured stem keeps its grade; unknown stem defaults to 0 (the
    # "return zeros on error" contract surface).
    assert result.grades == {"known-stem": 2, "unknown-stem": 0}, (
        f"missing stems must default to 0; got {result.grades!r}"
    )


def test_calibrate_returns_empty_when_calibration_disabled() -> None:
    """``calibrate`` returns False when configured to fail — downstream
    callers gate eval reads on this bool (no eval data published when
    the calibration suite trips).

    Sabotage proof: in ``FakeLLMJudge.calibrate`` change
    ``return self._calibration_passed`` to ``return True``. Re-run:
    ``is False`` assertion fails. Restored.
    """
    judge = FakeLLMJudge(calibration_passed=False)
    assert judge.calibrate() is False
