"""Memory consolidation pass — Plan B-parity Capability #4.

Promotes the existing :mod:`kairix.use_cases.contradict` pattern to
*ingest-time*: every newly extracted :class:`FactRecord` is compared to
the live facts that share its ``(entity, attribute, namespace)`` key,
and any contradicting prior fact is marked superseded. This is the
inner-loop counterpart of the full-corpus contradiction-audit use case;
running at ingest time keeps the live fact graph monotonically
converging on the operator's most recent ground truth.

Design contract:

- **Dependency injection is total.** Both the :class:`FactStore` and the
  per-pair ``contradict`` callable are constructor-injected. Tests pass
  a fake store from ``tests/fakes.py`` and a scripted callable; the CLI
  layer wires the production store + the default value-comparison
  callable defined below. F1: no monkeypatching.
- **Surface C signal.** Every supersession emits a structured INFO log
  line (``entity``/``attribute``/``old_id``/``new_id``/``verdict``) so
  the outer-loop learning surface can aggregate consolidation events
  without reaching into the fact store.
- **Idempotency.** :meth:`ConsolidationPass.process` calls
  :meth:`FactStore.supersede` only for prior facts the callable
  classifies as ``"update"`` or ``"contradiction"``. ``"same"`` verdicts
  pass through to :attr:`ConsolidationOutcome.coexists_with` so callers
  can audit no-op passes.

See ``docs/architecture/fitness-functions.md`` for the F1/F5/F26 rules
this module respects. No imports of ``kairix.providers`` or
``kairix.transport`` (F26).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from kairix.core.protocols import FactRecord, FactStore

logger = logging.getLogger(__name__)

__all__ = [
    "ConsolidationOutcome",
    "ConsolidationPass",
    "ContradictCallable",
    "ContradictResult",
    "default_contradict",
]


# ContradictResult is the three-way verdict a contradict callable returns.
# - "same"          → values match; both records coexist; no supersession.
# - "update"        → the new fact updates the prior fact's value; supersede prior.
# - "contradiction" → the new fact contradicts the prior fact; supersede prior.
ContradictResult = Literal["same", "update", "contradiction"]

# ContradictCallable is the per-pair comparator the consolidation pass invokes.
# Signature: (prior, new) → verdict. Production wire-up defaults to
# :func:`default_contradict`; tests inject scripted callables to pin
# branch coverage without an LLM call.
ContradictCallable = Callable[[FactRecord, FactRecord], ContradictResult]


@dataclass(frozen=True)
class ConsolidationOutcome:
    """Aggregate outcome of one consolidation pass against a new fact.

    ``new_fact_id``: id of the fact the pass ran against (the freshly
    persisted fact). Always populated.

    ``superseded_ids``: ids of prior facts the pass marked superseded
    via :meth:`FactStore.supersede` — i.e. the callable returned
    ``"update"`` or ``"contradiction"`` for them.

    ``coexists_with``: ids of prior facts that remain live alongside
    the new fact (callable returned ``"same"``). Surfaced so callers
    can audit no-op consolidation passes.

    Both tuples preserve the iteration order of :meth:`FactStore.find_conflicts`.
    """

    new_fact_id: str
    superseded_ids: tuple[str, ...]
    coexists_with: tuple[str, ...]


def default_contradict(prior: FactRecord, new: FactRecord) -> ContradictResult:
    """Placeholder per-pair comparator until the kairix ``contradict`` LLM wire-up lands.

    Returns ``"same"`` when ``prior.value == new.value`` (the new fact
    duplicates the prior), else ``"update"`` (the new fact carries a
    different value for the same entity-attribute key, so the prior
    record is no longer the live ground truth).

    A future commit will replace this with the LLM-driven ``contradict``
    use case so semantic equivalence (``"Acme Inc"`` vs ``"Acme, Inc."``)
    is recognised as ``"same"`` and direct denials as ``"contradiction"``.
    """
    return "same" if prior.value == new.value else "update"


class ConsolidationPass:
    """Inner-loop consolidation runner over a configured :class:`FactStore`.

    Each call to :meth:`process` runs once against a single new fact:
    fetches live priors for the same ``(entity, attribute, namespace)``
    key via :meth:`FactStore.find_conflicts`, invokes the configured
    callable per prior, marks each ``update`` / ``contradiction`` prior
    superseded, and returns a :class:`ConsolidationOutcome` summarising
    what changed.

    Production wire-up at the CLI layer constructs
    ``ConsolidationPass(fact_store=..., contradict=default_contradict)``.
    Tests inject a fake store + scripted callable.
    """

    def __init__(
        self,
        *,
        fact_store: FactStore,
        contradict: ContradictCallable,
    ) -> None:
        """Bind the configured store and per-pair comparator."""
        self._fact_store = fact_store
        self._contradict = contradict

    def process(self, new_fact: FactRecord) -> ConsolidationOutcome:
        """Consolidate ``new_fact`` against live priors; supersede contradictors.

        The pass treats the ``(entity, attribute, namespace)`` triple as
        the consolidation key — facts in different namespaces are
        engagement-isolated and never considered conflicts. ``new_fact``
        itself is filtered from the prior list (a fact never supersedes
        itself even if the store returns it from ``find_conflicts``).
        """
        existing = self._fact_store.find_conflicts(
            entity=new_fact.entity,
            attribute=new_fact.attribute,
            namespace=new_fact.namespace,
        )
        superseded: list[str] = []
        coexist: list[str] = []
        for prior in existing:
            if prior.id == new_fact.id:
                # The store returned the new fact itself (it was just
                # added). Skip — a fact never supersedes itself.
                continue
            verdict = self._contradict(prior, new_fact)
            if verdict in ("update", "contradiction"):
                self._fact_store.supersede(old_id=prior.id, new_id=new_fact.id)
                superseded.append(prior.id)
                logger.info(
                    "consolidation.supersede entity=%s attribute=%s old=%s new=%s verdict=%s",
                    new_fact.entity,
                    new_fact.attribute,
                    prior.id,
                    new_fact.id,
                    verdict,
                )
            else:
                coexist.append(prior.id)
        return ConsolidationOutcome(
            new_fact_id=new_fact.id,
            superseded_ids=tuple(superseded),
            coexists_with=tuple(coexist),
        )
