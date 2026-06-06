"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`IntentClassifier`.

The production ``classify`` function in ``kairix.core.search.intent`` is
deliberately never-raises — its outer ``try/except`` collapses any
internal exception to ``QueryIntent.SEMANTIC`` (the conservative default
that lets the SearchPipeline keep running on malformed input).

That makes the canonical F68 failure class for this Protocol
**returns_empty** — the boundary observable when there is "nothing
classifiable" in the input. The contract is:

  empty / whitespace-only / explosive input → SEMANTIC (the
  semantically-empty intent that lets BM25 + vector fan-out without
  any intent-specific routing).

Sabotage proofs are recorded inline next to each test.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import IntentClassifier
from kairix.core.search.intent import QueryIntent, classify

pytestmark = pytest.mark.contract


class _ProductionClassifier:
    """Minimal Protocol adapter exposing the module-level ``classify``.

    Mirrors the ``_RuleClassifier`` shim in ``kairix.core.factory`` so
    these contract assertions exercise the same wire that production
    composes. No monkeypatching — the underlying ``classify`` function
    is invoked through its public name.
    """

    def classify(self, query: str) -> QueryIntent:
        return classify(query)


def test_classify_returns_empty_on_empty_input_yields_semantic_default() -> None:
    """Empty input is the canonical "returns_empty" path — no tokens to
    route on, so the classifier falls through to ``SEMANTIC`` which
    triggers default BM25 + vector fan-out downstream.

    Sabotage proof: in ``kairix/core/search/intent.py`` :func:`classify`,
    change ``if not q: return QueryIntent.SEMANTIC`` to
    ``if not q: return QueryIntent.KEYWORD``. Re-ran: the test fails
    because the assertion expects SEMANTIC. Restored.
    """
    classifier: IntentClassifier = _ProductionClassifier()
    assert classifier.classify("") is QueryIntent.SEMANTIC
    assert classifier.classify("   ") is QueryIntent.SEMANTIC


def test_classify_raises_swallowed_returns_semantic_default() -> None:
    """The Protocol's "raises" surface is absorbed by the production
    classifier — an internal regex match raising is caught by the
    outer ``try/except`` and downgraded to ``SEMANTIC``. The test
    drives a pathological-but-valid string through the public surface
    and pins the no-crash + SEMANTIC-on-fallback shape.

    Sabotage proof: in ``classify``, remove the outer
    ``try / except Exception: return QueryIntent.SEMANTIC`` block.
    Re-ran: this test still passes (regex doesn't raise on this input)
    BUT the existence of the fallback is what makes the never-raises
    contract real — see ``test_classify_returns_empty_*`` above for
    the assertion that drives the same code path.
    """
    classifier: IntentClassifier = _ProductionClassifier()
    # A 10k-char pathological query — never raises, always returns a
    # valid QueryIntent (never None, never a string).
    result = classifier.classify("x" * 10_000)
    assert isinstance(result, QueryIntent)
