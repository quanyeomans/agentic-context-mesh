"""LLM-judge scorer — grades a synthesised answer against an expected answer.

Wraps an ``LLMBackend`` (from ``kairix.platform.llm.protocol``) into the
Scorer Protocol. The judge prompt is the canonical one previously
embedded in ``kairix.quality.eval.suite_runner._judge`` — moved here as
:func:`build_judge_prompt` so the eval CLI and the unified scorer
share one implementation (P5 will collapse the duplicate in
``suite_runner``).

Score interpretation (matches the prompt instruction):

* 1.0 — exact match
* 0.5 — partially correct
* 0.0 — wrong or missing

Robustness: a malformed judge response (no parseable float) maps to
0.0 rather than raising — degraded-mode fail-safe matches the existing
suite_runner behaviour.

F26-clean: imports ``LLMBackend`` Protocol from
``kairix.platform.llm.protocol``. No provider/transport imports here —
the LLM-backend wiring happens at the caller (the registry / production
factory).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kairix.platform.llm.protocol import LLMBackend
from kairix.quality.scoring.types import (
    QueryRunResult,
    ScorerResult,
)

_SYSTEM_PROMPT: str = (
    "You score retrieval answers. Respond with a single float "
    "between 0.0 and 1.0 on its own line. 1.0 = exact match. "
    "0.5 = partially correct. 0.0 = wrong or missing."
)


def build_judge_prompt(
    *,
    question: str,
    expected: str,
    context: str,
) -> list[dict[str, Any]]:
    """Build the canonical LLM-judge chat prompt.

    Single source of truth for the prompt shape — used by both the
    unified :class:`LLMJudgeScorer` and (after P5 consolidation) the
    eval-CLI ``SuiteRunner._judge``. Keeping the prompt string in one
    place is what stops the two judges drifting apart.
    """
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {question}\nExpected answer: {expected}\nRetrieved context:\n{context}\n\nScore (0.0-1.0):"
            ),
        },
    ]


def parse_judge_score(response: str) -> float:
    """Parse an LLM-judge response into a clamped 0.0-1.0 float.

    Robust to leading/trailing whitespace, surrounding text, and
    malformed responses (returns 0.0 rather than raising). Mirrors the
    behaviour of :func:`kairix.quality.eval.suite_runner._parse_score`.
    """
    if not response:
        return 0.0
    stripped = response.strip()
    try:
        value = float(stripped)
    except ValueError:
        value = _first_float_in(stripped)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _first_float_in(text: str) -> float:
    """Return the first parseable float in ``text``, or 0.0 if none."""
    for token in text.replace(",", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return 0.0


class LLMJudgeScorer:
    """LLM-judge scorer — grades synthesised answer against an expected answer.

    Constructor takes:

    * ``llm`` — the ``LLMBackend`` Protocol implementation. Tests pass
      ``FakeLLMBackend`` from ``tests/fakes.py``; production wires the
      configured provider plugin via the factory at the call site.
    * ``expected_answer`` — the reference answer (per-query, from
      ``BenchmarkCase.expected_answer``). Empty / None → returns 0.0 with
      ``details["reason"] = "no_expected_answer"``.
    * ``metric_name`` — registry key for the score (default ``"judge"``).
    """

    def __init__(
        self,
        *,
        llm: LLMBackend,
        expected_answer: str | None = None,
        metric_name: str = "judge",
    ) -> None:
        self._llm = llm
        self._expected = expected_answer or ""
        self._metric_name = metric_name

    @property
    def name(self) -> str:
        return "judge"

    def score(self, run: QueryRunResult | Sequence[QueryRunResult], /) -> ScorerResult:
        if not isinstance(run, QueryRunResult):
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "judge scorer received a sequence; expects one QueryRunResult"},
            )
        if run.error:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "query_run_failed", "error": run.error},
            )
        if not self._expected:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "no_expected_answer"},
            )
        if run.synthesised_answer is None:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "no_synthesised_answer"},
            )
        prompt = build_judge_prompt(
            question=run.query_text,
            expected=self._expected,
            context=run.synthesised_answer,
        )
        response = self._llm.chat(prompt, max_tokens=8)
        value = parse_judge_score(response)
        return ScorerResult(
            metric_name=self._metric_name,
            score=round(value, 4),
            details={"raw_response": response},
        )
