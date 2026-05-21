"""ScorerRegistry + auto_select_scorers helper.

The registry is a thin name → Scorer mapping that the mode dispatcher
(P3) and the reporter (P5) use to look up scorers by stable string key.
It exists so a YAML suite can declare ``scorers: [ndcg, hit_at_k, mrr]``
or, equivalently, the runner can call ``auto_select_scorers(suite,
results)`` which picks the right set based on which suite fields are
populated:

* ``gold_titles`` present on any case → enable ``ndcg``, ``hit_at_k``, ``mrr``.
* ``expected_answer`` present on any case → enable ``judge``.
* ``latency_ms`` populated on any result → enable ``latency``.

The selection is conservative — it adds scorers; it never *omits* one
just because a single case lacks the relevant field. Per-case
empty-input handling at the scorer level (returning 0.0 with reason)
is the right place to handle the missing-gold partial case.

F26-clean: imports concrete scorers from this package and nothing
provider/transport-shaped. The judge wiring is conditional on an
``LLMBackend`` being passed by the caller (production: from the
configured provider plugin via the factory; tests: ``FakeLLMBackend``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from kairix.quality.scoring.hit_at_k import HitAtKScorer
from kairix.quality.scoring.latency import LatencyScorer
from kairix.quality.scoring.llm_judge import LLMJudgeScorer
from kairix.quality.scoring.mrr import MRRScorer
from kairix.quality.scoring.ndcg import NDCGScorer
from kairix.quality.scoring.types import QueryRunResult, Scorer

if TYPE_CHECKING:
    from kairix.platform.llm.protocol import LLMBackend


class ScorerRegistry:
    """Name → Scorer mapping with lookup, listing, and replacement.

    Tests and the production reporter use the same registry; concrete
    Scorer instances are constructed at registration time (with their
    gold + backend dependencies pre-bound) so the lookup is just a
    dict access.
    """

    def __init__(self, scorers: Iterable[Scorer] | None = None) -> None:
        self._scorers: dict[str, Scorer] = {}
        for s in scorers or ():
            self.register(s)

    def register(self, scorer: Scorer) -> None:
        """Insert or replace a scorer keyed by its ``name`` property."""
        self._scorers[scorer.name] = scorer

    def get(self, name: str) -> Scorer:
        """Return the scorer registered under ``name`` or raise KeyError.

        Error message follows the F21 affordance template — names the
        missing scorer and lists what IS registered.
        """
        if name in self._scorers:
            return self._scorers[name]
        known = ", ".join(sorted(self._scorers)) or "(none)"
        raise KeyError(
            f"scorer {name!r} not registered. "
            f"fix: call ScorerRegistry.register(<scorer>) before lookup. "
            f"next: registered scorers are: {known}. "
            f"run: see kairix/quality/scoring/README.md for the registry contract."
        )

    def names(self) -> tuple[str, ...]:
        """Return the registered scorer names in sorted order."""
        return tuple(sorted(self._scorers))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._scorers

    def __len__(self) -> int:
        return len(self._scorers)


def auto_select_scorers(
    *,
    cases: Iterable[Mapping[str, Any]],
    results: Iterable[QueryRunResult] | None = None,
    llm: LLMBackend | None = None,
) -> ScorerRegistry:
    """Build a ScorerRegistry from suite shape + run shape.

    ``cases`` is the iterable of suite case dicts (e.g.
    ``BenchmarkSuite.cases`` converted to dicts, OR raw YAML rows).
    The function reads the per-case fields ``gold_titles`` and
    ``expected_answer`` to decide which scorers to include.

    ``results`` is optional — when provided, the function also inspects
    whether ``latency_ms`` was populated on any run and enables
    ``LatencyScorer`` if so.

    ``llm`` is required only when ``expected_answer`` is present on at
    least one case (to wire the judge). When the judge is needed and
    ``llm`` is None, the function raises ValueError with an actionable
    message — never silently skip the judge.

    Returns a registry pre-populated with one scorer per detected
    capability. The mode dispatcher / reporter then iterates the
    registry per QueryRunResult, materialising the per-query
    ScorerResult set.

    Per-case gold + expected_answer are NOT baked into the registry
    instances — the auto-selector returns generic scorers (no per-case
    gold). The caller binds per-case gold at score time by constructing
    fresh scorers per query (see P3 mode dispatcher) OR by carrying the
    gold through the result envelope. This separation keeps the registry
    declarative.
    """
    cases_list = list(cases)
    has_gold = any(_has_gold(c) for c in cases_list)
    has_expected = any(c.get("expected_answer") for c in cases_list)
    has_latency = bool(results) and any(r.latency_ms > 0 for r in (results or ()))

    registry = ScorerRegistry()
    if has_gold:
        registry.register(NDCGScorer())
        registry.register(HitAtKScorer())
        registry.register(MRRScorer())
    if has_expected:
        if llm is None:
            raise ValueError(
                "expected_answer present on at least one case but no LLMBackend supplied. "
                "fix: pass llm=<LLMBackend> to auto_select_scorers(...). "
                "next: production wires the configured provider plug-in via the "
                "factory; tests pass FakeLLMBackend from tests/fakes.py. "
                "run: see kairix/quality/scoring/README.md."
            )
        registry.register(LLMJudgeScorer(llm=llm, expected_answer=""))
    if has_latency:
        registry.register(LatencyScorer())
    return registry


def _has_gold(case: Mapping[str, Any]) -> bool:
    """True when the case carries either a ``gold_titles`` or ``gold_paths`` list."""
    return bool(case.get("gold_titles") or case.get("gold_paths"))
