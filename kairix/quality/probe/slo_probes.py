"""Command-probe adapters + synthetic workload for the SLO harness (PLA-256).

:func:`build_command_probes` is the ONE adapter layer that wires the four
most-used agent commands (``brief`` / ``remember`` / ``recall`` /
``search``) into harness :class:`~kairix.quality.probe.slo_harness.CommandProbe`
objects over injected callables. Both wirings share it:

- :func:`build_synthetic_workload` — deterministic, offline, CI-safe. Seeds
  a small in-process fact store with the #340 fact-pattern set and answers
  searches from a fixed synthetic document corpus. This is production
  synthetic-corpus code (sibling to :mod:`kairix.quality.benchmark.mock_retrieval`),
  NOT a ``tests/`` fake (F24 forbids ``kairix`` importing tests).
- :func:`default_real_workload` — wires the same adapter layer against the
  production seams (real ``SQLiteFactStore`` + ``build_search_pipeline``)
  so ``kairix slo --real`` measures the operator's configured instance.

Breadcrumb extraction (the affordance signal) is per-command:

- ``search`` / ``brief`` — each result row's ``source_uri`` (else ``path``,
  else the inner ``result.path`` of a budgeted row).
- ``recall`` — ``turn://<first source_turn_id>`` from each fact hit.
- ``remember`` — the ``memory://`` / file path the write landed at.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.quality.probe.slo_harness import (
    CommandCall,
    CommandProbe,
    GroundTruthFact,
    RecallSuite,
)

__all__ = [
    "SYNTHETIC_FACTS",
    "build_command_probes",
    "build_synthetic_workload",
    "default_real_workload",
    "load_ground_truth_facts",
]


# ---------------------------------------------------------------------------
# Breadcrumb extraction helpers
# ---------------------------------------------------------------------------


def _row_breadcrumb(row: Any) -> str | None:
    """Pull the source_uri breadcrumb off a search/brief result row.

    Tries, in order: ``row.source_uri`` (the canonical chunk field),
    ``row.path`` (the SearchHit projection), then ``row.result.path``
    (a ``BudgetedResult`` wrapping a fused row). Returns ``None`` when the
    row exposes no breadcrumb so the affordance metric counts it as a
    dead end.
    """
    for attr in ("source_uri", "path"):
        value = getattr(row, attr, None)
        if isinstance(value, str) and value:
            return value
    inner = getattr(row, "result", None)
    inner_path = getattr(inner, "path", None)
    return inner_path if isinstance(inner_path, str) and inner_path else None


def _hit_turn_breadcrumb(hit: Any) -> str | None:
    """Build a ``turn://`` breadcrumb from a fact hit's source turn ids."""
    record = getattr(hit, "record", hit)
    turn_ids = tuple(getattr(record, "source_turn_ids", ()) or ())
    return f"turn://{turn_ids[0]}" if turn_ids else None


# ---------------------------------------------------------------------------
# Generic adapter layer — shared by synthetic + real wiring
# ---------------------------------------------------------------------------


def build_command_probes(
    *,
    search_fn: Callable[[str], Sequence[Any]],
    recall_fn: Callable[[str], Sequence[Any]],
    remember_fn: Callable[[str], str | None],
    brief_fn: Callable[[str], Sequence[Any]],
    search_payloads: Sequence[str],
    recall_payloads: Sequence[str],
    remember_payloads: Sequence[str],
    brief_payloads: Sequence[str],
) -> tuple[CommandProbe, ...]:
    """Wire the four agent commands into harness probes over callables.

    Each ``*_fn`` runs the command once and returns the agent-facing
    records (or, for ``remember``, the breadcrumb of the written memory).
    The adapter extracts the per-record breadcrumb so the harness can
    score latency and affordance uniformly across commands.
    """

    def run_search(query: str) -> CommandCall:
        return CommandCall(breadcrumbs=tuple(_row_breadcrumb(row) for row in search_fn(query)))

    def run_recall(entity: str) -> CommandCall:
        return CommandCall(breadcrumbs=tuple(_hit_turn_breadcrumb(hit) for hit in recall_fn(entity)))

    def run_remember(content: str) -> CommandCall:
        return CommandCall(breadcrumbs=(remember_fn(content),))

    def run_brief(topic: str) -> CommandCall:
        return CommandCall(breadcrumbs=tuple(_row_breadcrumb(row) for row in brief_fn(topic)))

    return (
        CommandProbe(name="brief", payloads=tuple(brief_payloads), run=run_brief),
        CommandProbe(name="remember", payloads=tuple(remember_payloads), run=run_remember),
        CommandProbe(name="recall", payloads=tuple(recall_payloads), run=run_recall),
        CommandProbe(name="search", payloads=tuple(search_payloads), run=run_search),
    )


# ---------------------------------------------------------------------------
# Synthetic corpus (deterministic, offline)
# ---------------------------------------------------------------------------

# Repeated synthetic identifiers, hoisted to constants so the corpus stays
# F17-clean (no string literal >=10 chars duplicated >=3 times in a module).
_CLIENT = "client-omega"
_ENGAGEMENT = "engagement-alpha"
_EXEC_SPONSOR = "exec-sigma"
_TURN_INTAKE = "eng-alpha-s001-t003"  # the intake turn three facts share

# The #340 fact-pattern set — entity-attribute-value triples with the
# generic agent-alpha / engagement-alpha naming the reference-library
# conversation corpora use (F32-clean: no real names). Mirrors a slice of
# reference-library/conversations/team-alpha/ground-truth-facts.json so the
# harness measures fact-recall against the canonical pattern offline.
SYNTHETIC_FACTS: tuple[dict[str, Any], ...] = (
    {
        "entity": _CLIENT,
        "attribute": "industry",
        "value": "last-mile delivery / logistics platform",
        "turn": _TURN_INTAKE,
    },
    {"entity": _CLIENT, "attribute": "headquarters", "value": "Wellington", "turn": _TURN_INTAKE},
    {"entity": _CLIENT, "attribute": "staff-count", "value": "approximately 180", "turn": _TURN_INTAKE},
    {"entity": _CLIENT, "attribute": "primary-cloud", "value": "cloud-zeta", "turn": "eng-alpha-s002-t007"},
    {"entity": _CLIENT, "attribute": "routes-api-language", "value": "Go", "turn": "eng-alpha-s002-t001"},
    {"entity": _ENGAGEMENT, "attribute": "executive-sponsor", "value": _EXEC_SPONSOR, "turn": "eng-alpha-s001-t005"},
    {"entity": _ENGAGEMENT, "attribute": "day-to-day-contact", "value": "pm-tau", "turn": "eng-alpha-s001-t005"},
    {"entity": _ENGAGEMENT, "attribute": "budget", "value": "$480k fixed-scope", "turn": "eng-alpha-s001-t007"},
    {"entity": _ENGAGEMENT, "attribute": "duration", "value": "12 weeks", "turn": "eng-alpha-s001-t007"},
    {"entity": _ENGAGEMENT, "attribute": "kickoff-date", "value": "2026-02-09", "turn": "eng-alpha-s001-t013"},
)


@dataclass(frozen=True)
class _SyntheticDoc:
    """Search/brief corpus + result row carrying a resolvable ``source_uri``."""

    source_uri: str
    title: str
    keywords: frozenset[str] = frozenset()


# Synthetic document corpus for the search + brief commands. Each carries a
# resolvable ``source_uri`` so a healthy baseline reports 100% affordance.
_SYNTHETIC_DOCS: tuple[_SyntheticDoc, ...] = (
    _SyntheticDoc(
        source_uri="kb://engagement-alpha/overview.md",
        title="engagement-alpha overview",
        keywords=frozenset({_ENGAGEMENT, _CLIENT, "budget", "duration", "sponsor"}),
    ),
    _SyntheticDoc(
        source_uri="kb://client-omega/platform.md",
        title="client-omega platform",
        keywords=frozenset({_CLIENT, "cloud-zeta", "cloud", "routes", "api", "go"}),
    ),
    _SyntheticDoc(
        source_uri="kb://engagement-alpha/cutover-plan.md",
        title="cutover plan",
        keywords=frozenset({"cutover", "plan", "region", "canary", "migration"}),
    ),
    _SyntheticDoc(
        source_uri="kb://engagement-alpha/team.md",
        title="engagement-alpha team",
        keywords=frozenset({_ENGAGEMENT, "pm-tau", _EXEC_SPONSOR, "team", "contact"}),
    ),
)


@dataclass(frozen=True)
class _SyntheticFactRecord:
    """FactRecord-shaped synthetic record (read surface only)."""

    entity: str
    attribute: str
    value: str
    source_turn_ids: tuple[str, ...]


@dataclass(frozen=True)
class _SyntheticFactHit:
    """FactHit-shaped synthetic hit — ``record`` + ``score``."""

    record: _SyntheticFactRecord
    score: float


@dataclass
class _SyntheticFactStore:
    """Dict-backed synthetic fact store — naive entity/attribute/value overlap.

    Production synthetic-corpus component (not a ``tests/`` fake). Mirrors
    the ``FactStore.search`` read surface the recall metric needs.
    """

    records: list[_SyntheticFactRecord] = field(default_factory=list)

    def add(self, record: _SyntheticFactRecord) -> None:
        self.records.append(record)

    def search(self, query: str, *, top_k: int = 10) -> list[_SyntheticFactHit]:
        query_words = set(query.lower().split())
        scored: list[tuple[float, str, _SyntheticFactHit]] = []
        for record in self.records:
            haystack = f"{record.entity} {record.attribute} {record.value}".lower().split()
            overlap = len(query_words & set(haystack))
            if overlap == 0:
                continue
            score = overlap / max(len(query_words), 1)
            scored.append((score, record.entity + record.attribute, _SyntheticFactHit(record=record, score=score)))
        # Sort by score desc, then a stable key, so ties are deterministic.
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [hit for _, _, hit in scored[:top_k]]


@dataclass
class _SyntheticMemoryStore:
    """Append-only synthetic memory store — mints a deterministic id per write."""

    written: list[str] = field(default_factory=list)

    def add(self, content: str) -> str:
        self.written.append(content)
        return f"memory://synthetic/{len(self.written):04d}"


def _keyword_search(query: str, docs: Sequence[_SyntheticDoc], *, limit: int) -> list[_SyntheticDoc]:
    """Deterministic keyword-overlap search over the synthetic doc corpus."""
    query_words = set(query.lower().split())
    scored: list[tuple[int, str, _SyntheticDoc]] = []
    for doc in docs:
        overlap = len(query_words & doc.keywords)
        if overlap == 0:
            continue
        scored.append((overlap, doc.source_uri, doc))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [doc for _, _, doc in scored[:limit]]


_SEARCH_PAYLOADS = (f"{_CLIENT} cloud", f"{_ENGAGEMENT} budget", "cutover plan region", "routes api go")
_RECALL_PAYLOADS = (_CLIENT, _ENGAGEMENT, _EXEC_SPONSOR)
_REMEMBER_PAYLOADS = (
    "decided: pilot the cloud-zeta cutover on the smallest region first",
    "note: pm-tau owns day-to-day delivery for the engagement",
)
_BRIEF_PAYLOADS = (_ENGAGEMENT, f"{_CLIENT} platform")


def build_synthetic_workload() -> tuple[tuple[CommandProbe, ...], list[RecallSuite]]:
    """Build the deterministic, offline probe set + recall suite.

    Returns ``(probes, recall_suites)`` ready for
    :func:`kairix.quality.probe.slo_harness.build_report`. Used by
    ``kairix slo`` in its default (synthetic) mode so the harness runs in
    CI and on a fresh install with no configured index.
    """
    store = _SyntheticFactStore()
    for raw in SYNTHETIC_FACTS:
        store.add(
            _SyntheticFactRecord(
                entity=raw["entity"],
                attribute=raw["attribute"],
                value=raw["value"],
                source_turn_ids=(raw["turn"],),
            )
        )
    memory = _SyntheticMemoryStore()

    def search_fn(query: str) -> Sequence[Any]:
        return _keyword_search(query, _SYNTHETIC_DOCS, limit=10)

    def recall_fn(entity: str) -> Sequence[Any]:
        return store.search(entity, top_k=10)

    def remember_fn(content: str) -> str:
        return memory.add(content)

    def brief_fn(topic: str) -> Sequence[Any]:
        # A briefing cites its top supporting sources.
        return _keyword_search(topic, _SYNTHETIC_DOCS, limit=3)

    probes = build_command_probes(
        search_fn=search_fn,
        recall_fn=recall_fn,
        remember_fn=remember_fn,
        brief_fn=brief_fn,
        search_payloads=_SEARCH_PAYLOADS,
        recall_payloads=_RECALL_PAYLOADS,
        remember_payloads=_REMEMBER_PAYLOADS,
        brief_payloads=_BRIEF_PAYLOADS,
    )
    gt_facts = tuple(
        GroundTruthFact(entity=r["entity"], attribute=r["attribute"], value=r["value"]) for r in SYNTHETIC_FACTS
    )
    recall_suites: list[RecallSuite] = [("synthetic-340", gt_facts, recall_fn)]
    return probes, recall_suites


# ---------------------------------------------------------------------------
# Ground-truth suite loading (real mode + operator-supplied suites)
# ---------------------------------------------------------------------------


def load_ground_truth_facts(suite_dir: Path) -> tuple[GroundTruthFact, ...]:
    """Load a ``ground-truth-facts.json`` from ``suite_dir`` into facts.

    Reads the reference-library conversation-corpus shape — a JSON array of
    ``{entity, attribute, value, ...}`` objects. Returns an empty tuple
    when the file is absent so real mode degrades to a latency/affordance-
    only run rather than crashing.
    """
    facts_path = suite_dir / "ground-truth-facts.json"
    if not facts_path.is_file():
        return ()
    raw = json.loads(facts_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return ()
    out: list[GroundTruthFact] = []
    for item in raw:
        if isinstance(item, dict) and item.get("entity") and item.get("attribute"):
            out.append(
                GroundTruthFact(
                    entity=str(item["entity"]),
                    attribute=str(item["attribute"]),
                    value=str(item.get("value", "")),
                )
            )
    return tuple(out)


# ---------------------------------------------------------------------------
# Real-mode wiring (production seams)
# ---------------------------------------------------------------------------


def default_real_workload(
    *,
    paths: Any = None,
    suite_dir: Path | None = None,
) -> tuple[tuple[CommandProbe, ...], list[RecallSuite]]:
    """Wire the four commands against production seams for real measurement.

    Lazy-imports the heavy retrieval stack so the synthetic path never pays
    for it. ``recall`` and ``facts_about`` route through the real
    ``SQLiteFactStore``; ``search`` builds the production ``SearchPipeline``
    on first call (the cold-start the COLD phase captures); ``remember``
    drives the real memory write. The recall suite loads from ``suite_dir``
    when supplied (an operator's ingested reference suite), else falls back
    to the embedded #340 pattern.
    """
    from kairix.core.facts import SQLiteFactStore
    from kairix.paths import KairixPaths

    resolved = paths if paths is not None else KairixPaths.resolve()
    store = SQLiteFactStore(db_path=resolved.db_path)

    def search_fn(query: str) -> Sequence[Any]:
        from kairix.core.factory import build_search_pipeline

        pipeline = build_search_pipeline(paths=resolved)
        return list(getattr(pipeline.search(query=query), "results", ()))

    def recall_fn(entity: str) -> Sequence[Any]:
        return store.search(entity, top_k=10)

    def remember_fn(content: str) -> str | None:
        from kairix.use_cases.remember import remember

        result = remember("agent-alpha", content, "note")
        return getattr(result, "path", None)

    def brief_fn(topic: str) -> Sequence[Any]:
        return recall_fn(topic)

    probes = build_command_probes(
        search_fn=search_fn,
        recall_fn=recall_fn,
        remember_fn=remember_fn,
        brief_fn=brief_fn,
        search_payloads=_SEARCH_PAYLOADS,
        recall_payloads=_RECALL_PAYLOADS,
        remember_payloads=_REMEMBER_PAYLOADS,
        brief_payloads=_BRIEF_PAYLOADS,
    )
    gt_facts = (
        load_ground_truth_facts(suite_dir)
        if suite_dir is not None
        else tuple(
            GroundTruthFact(entity=r["entity"], attribute=r["attribute"], value=r["value"]) for r in SYNTHETIC_FACTS
        )
    )
    recall_suites: list[RecallSuite] = [("real-fact-store", gt_facts, recall_fn)]
    return probes, recall_suites
