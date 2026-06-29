"""Unit tests for the SLO-harness command-probe adapters + synthetic workload.

Covers the ONE adapter layer (per-command breadcrumb extraction), the
deterministic synthetic corpus, the ground-truth loader, and the real-mode
wiring's construction path (against an empty real fact store). No
wall-clock-ceiling assertions (F82).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from kairix.quality.probe.slo_harness import GroundTruthFact, build_report
from kairix.quality.probe.slo_probes import (
    SYNTHETIC_FACTS,
    build_command_probes,
    build_synthetic_workload,
    default_real_workload,
    load_ground_truth_facts,
)
from tests.fakes import FakeFactHit, FakeFactRecord, FakePaths

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Synthetic workload
# ---------------------------------------------------------------------------


def test_build_synthetic_workload_wires_four_commands() -> None:
    """The synthetic workload exposes brief / remember / recall / search."""
    probes, recall_suites = build_synthetic_workload()
    assert tuple(p.name for p in probes) == ("brief", "remember", "recall", "search")
    assert recall_suites[0][0] == "synthetic-340"
    assert recall_suites[0][1][0].entity == SYNTHETIC_FACTS[0]["entity"]


def test_synthetic_workload_reports_perfect_recall_and_full_affordance() -> None:
    """End to end, the synthetic corpus is fully recallable and breadcrumbed.

    This is the deterministic baseline the CI outcome test relies on:
    every #340 fact is retrievable by its own (entity, attribute) and
    every agent-facing record carries a resolvable source_uri.
    """
    probes, recall_suites = build_synthetic_workload()
    report = build_report(probes=probes, recall_suites=recall_suites, concurrency_n=3, recall_k=5)

    assert report.recall[0].recall_at_k == 1.0
    assert report.recall[0].ndcg_at_k == 1.0
    assert report.recall[0].n_facts == len(SYNTHETIC_FACTS)
    assert all(a.pct_resolvable == 100.0 for a in report.affordance)


def test_synthetic_search_returns_relevant_docs_not_zero_overlap() -> None:
    """The synthetic search surfaces keyword-matching docs (pins relevance).

    A query about the client's cloud must cite the client platform doc and
    must NOT cite a zero-overlap doc (the cutover plan). Without this the
    keyword-overlap filter could invert and still report 100% affordance,
    because every doc carries a source_uri regardless of relevance.
    """
    probes, _suites = build_synthetic_workload()
    search = next(p for p in probes if p.name == "search")
    breadcrumbs = search.run(f"{SYNTHETIC_FACTS[3]['entity']} cloud").breadcrumbs
    assert "kb://client-omega/platform.md" in breadcrumbs
    assert "kb://engagement-alpha/cutover-plan.md" not in breadcrumbs


# ---------------------------------------------------------------------------
# build_command_probes — per-command breadcrumb extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RowWithSourceUri:
    source_uri: str


@dataclass(frozen=True)
class _RowWithPath:
    path: str


@dataclass(frozen=True)
class _Inner:
    path: str


@dataclass(frozen=True)
class _BudgetedRow:
    result: _Inner


@dataclass(frozen=True)
class _RowWithNothing:
    title: str = "no breadcrumb"


def _probes_over(
    *, search_fn=lambda _q: [], recall_fn=lambda _e: [], remember_fn=lambda _c: "memory://x", brief_fn=lambda _t: []
):
    return build_command_probes(
        search_fn=search_fn,
        recall_fn=recall_fn,
        remember_fn=remember_fn,
        brief_fn=brief_fn,
        search_payloads=("q",),
        recall_payloads=("e",),
        remember_payloads=("c",),
        brief_payloads=("t",),
    )


def _run(probes, name: str, payload: str):
    probe = next(p for p in probes if p.name == name)
    return probe.run(payload)


def test_search_breadcrumb_prefers_source_uri() -> None:
    probes = _probes_over(search_fn=lambda _q: [_RowWithSourceUri(source_uri="kb://doc.md")])
    assert _run(probes, "search", "q").breadcrumbs == ("kb://doc.md",)


def test_search_breadcrumb_falls_back_to_path() -> None:
    probes = _probes_over(search_fn=lambda _q: [_RowWithPath(path="vault/a.md")])
    assert _run(probes, "search", "q").breadcrumbs == ("vault/a.md",)


def test_search_breadcrumb_reads_budgeted_inner_path() -> None:
    probes = _probes_over(search_fn=lambda _q: [_BudgetedRow(result=_Inner(path="facts://1"))])
    assert _run(probes, "search", "q").breadcrumbs == ("facts://1",)


def test_search_breadcrumb_none_when_row_has_no_uri() -> None:
    probes = _probes_over(search_fn=lambda _q: [_RowWithNothing()])
    assert _run(probes, "search", "q").breadcrumbs == (None,)


def test_search_breadcrumb_none_when_source_uri_is_empty() -> None:
    """An empty source_uri is a dead end, not a breadcrumb (pins the truthy guard)."""
    probes = _probes_over(search_fn=lambda _q: [_RowWithSourceUri(source_uri="")])
    assert _run(probes, "search", "q").breadcrumbs == (None,)


def test_search_breadcrumb_none_when_budgeted_inner_path_is_empty() -> None:
    """An empty inner result.path is a dead end (pins the budgeted-row truthy guard)."""
    probes = _probes_over(search_fn=lambda _q: [_BudgetedRow(result=_Inner(path=""))])
    assert _run(probes, "search", "q").breadcrumbs == (None,)


def test_recall_breadcrumb_builds_turn_uri() -> None:
    hit = FakeFactHit(
        record=FakeFactRecord(
            id="f1",
            entity="client-omega",
            attribute="industry",
            value="logistics",
            source_turn_ids=("eng-alpha-s001-t003",),
        ),
        score=1.0,
    )
    probes = _probes_over(recall_fn=lambda _e: [hit])
    assert _run(probes, "recall", "client-omega").breadcrumbs == ("turn://eng-alpha-s001-t003",)


def test_recall_breadcrumb_none_without_turn_ids() -> None:
    hit = FakeFactHit(
        record=FakeFactRecord(id="f1", entity="x", attribute="y", value="z", source_turn_ids=()), score=1.0
    )
    probes = _probes_over(recall_fn=lambda _e: [hit])
    assert _run(probes, "recall", "x").breadcrumbs == (None,)


def test_remember_breadcrumb_is_the_memory_uri() -> None:
    probes = _probes_over(remember_fn=lambda _c: "memory://synthetic/0007")
    assert _run(probes, "remember", "decided: ...").breadcrumbs == ("memory://synthetic/0007",)


def test_brief_breadcrumb_extracts_cited_sources() -> None:
    probes = _probes_over(brief_fn=lambda _t: [_RowWithSourceUri(source_uri="kb://brief-src.md")])
    assert _run(probes, "brief", "topic").breadcrumbs == ("kb://brief-src.md",)


# ---------------------------------------------------------------------------
# load_ground_truth_facts
# ---------------------------------------------------------------------------


def test_load_ground_truth_facts_reads_triples(tmp_path: Path) -> None:
    (tmp_path / "ground-truth-facts.json").write_text(
        json.dumps(
            [
                {"entity": "client-omega", "attribute": "industry", "value": "logistics"},
                {"entity": "engagement-alpha", "attribute": "budget", "value": "$480k"},
            ]
        ),
        encoding="utf-8",
    )
    facts = load_ground_truth_facts(tmp_path)
    assert len(facts) == 2
    assert facts[0] == GroundTruthFact(entity="client-omega", attribute="industry", value="logistics")


def test_load_ground_truth_facts_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_ground_truth_facts(tmp_path) == ()


def test_load_ground_truth_facts_non_list_is_empty(tmp_path: Path) -> None:
    (tmp_path / "ground-truth-facts.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert load_ground_truth_facts(tmp_path) == ()


def test_load_ground_truth_facts_skips_incomplete_rows(tmp_path: Path) -> None:
    (tmp_path / "ground-truth-facts.json").write_text(
        json.dumps([{"entity": "x", "attribute": "y", "value": "z"}, {"entity": "", "attribute": "y"}]),
        encoding="utf-8",
    )
    facts = load_ground_truth_facts(tmp_path)
    assert len(facts) == 1


# ---------------------------------------------------------------------------
# default_real_workload — real-mode construction over an empty fact store
# ---------------------------------------------------------------------------


def test_default_real_workload_wires_four_commands_over_real_store(tmp_path: Path) -> None:
    """Real mode wires the four commands against a real (empty) fact store.

    recall + brief route through the real ``SQLiteFactStore.search`` — on a
    fresh DB that returns no hits and no error, so the adapters yield empty
    breadcrumb tuples.
    """
    paths = FakePaths(db_path=tmp_path / "facts.db", document_root=tmp_path / "vault")
    probes, recall_suites = default_real_workload(paths=paths)

    assert tuple(p.name for p in probes) == ("brief", "remember", "recall", "search")
    assert recall_suites[0][0] == "real-fact-store"
    assert recall_suites[0][1][0].entity == SYNTHETIC_FACTS[0]["entity"]

    recall_probe = next(p for p in probes if p.name == "recall")
    assert recall_probe.run("client-omega").breadcrumbs == ()
    brief_probe = next(p for p in probes if p.name == "brief")
    assert brief_probe.run("engagement-alpha").breadcrumbs == ()


def test_default_real_workload_loads_suite_dir_ground_truth(tmp_path: Path) -> None:
    """A --suite-dir override loads the operator's labelled facts for recall."""
    (tmp_path / "ground-truth-facts.json").write_text(
        json.dumps([{"entity": "client-omega", "attribute": "industry", "value": "logistics"}]),
        encoding="utf-8",
    )
    paths = FakePaths(db_path=tmp_path / "facts.db", document_root=tmp_path / "vault")
    _probes, recall_suites = default_real_workload(paths=paths, suite_dir=tmp_path)
    _name, facts, _fn = recall_suites[0]
    assert len(facts) == 1
    assert facts[0].entity == "client-omega"
