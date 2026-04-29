"""Step definitions for benchmark_run.feature."""

from pytest_bdd import given, parsers, then, when

from kairix.quality.benchmark.runner import BenchmarkResult, run_benchmark
from kairix.quality.benchmark.suite import BenchmarkCase, BenchmarkSuite

# Module-level state (simple, test-scoped)
_state: dict = {}


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("a valid benchmark suite with recall and entity cases")
def suite_with_recall_and_entity() -> None:
    """Build a small suite with recall and entity cases against the mock corpus."""
    _state["suite"] = BenchmarkSuite(
        meta={"name": "bdd-basic", "version": "1.0"},
        cases=[
            BenchmarkCase(
                id="R01",
                category="recall",
                query="hybrid search retrieval architecture BM25 vector",
                gold_path="concept/retrieval-architecture.md",
                score_method="exact",
            ),
            BenchmarkCase(
                id="E01",
                category="entity",
                query="entity graph Neo4j knowledge nodes mentions",
                gold_path="concept/entity-graph.md",
                score_method="exact",
            ),
        ],
    )


@given("a benchmark suite where all gold paths match mock results")
def suite_perfect_match() -> None:
    """Build a suite using queries whose keywords will surface the gold docs
    in the mock fixture's top-5 results for every scored category."""
    _state["suite"] = BenchmarkSuite(
        meta={"name": "bdd-perfect", "version": "1.0"},
        cases=[
            # recall — keywords strongly target retrieval-architecture.md
            BenchmarkCase(
                id="P-R01",
                category="recall",
                query="hybrid retrieval architecture search BM25 vector RRF",
                gold_path="concept/retrieval-architecture.md",
                score_method="exact",
            ),
            # temporal — keywords target 2026-03-15-standup.md
            BenchmarkCase(
                id="P-T01",
                category="temporal",
                query="2026-03-15 march standup meeting notes daily",
                gold_path="notes/2026-03-15-standup.md",
                score_method="exact",
            ),
            # entity — keywords target entity-graph.md
            BenchmarkCase(
                id="P-E01",
                category="entity",
                query="entity graph Neo4j relationship knowledge nodes mentions",
                gold_path="concept/entity-graph.md",
                score_method="exact",
            ),
            # conceptual — keywords target semantic-search.md
            BenchmarkCase(
                id="P-C01",
                category="conceptual",
                query="semantic search dense embedding conceptual similarity meaning",
                gold_path="concept/semantic-search.md",
                score_method="exact",
            ),
            # multi_hop — keywords target multi-hop-queries.md
            BenchmarkCase(
                id="P-M01",
                category="multi_hop",
                query="multi-hop multi hop query planning decompose sub-query",
                gold_path="concept/multi-hop-queries.md",
                score_method="exact",
            ),
            # procedural — keywords target deploy-checklist.md
            BenchmarkCase(
                id="P-P01",
                category="procedural",
                query="deploy deployment checklist runbook procedure steps production",
                gold_path="runbooks/deploy-checklist.md",
                score_method="exact",
            ),
        ],
    )


@given("a benchmark suite where no gold paths match mock results")
def suite_zero_match() -> None:
    """Build a suite with gold paths that do not exist in the mock fixture."""
    _state["suite"] = BenchmarkSuite(
        meta={"name": "bdd-zero", "version": "1.0"},
        cases=[
            BenchmarkCase(
                id="Z-R01",
                category="recall",
                query="hybrid search retrieval",
                gold_path="nonexistent/does-not-exist-alpha.md",
                score_method="exact",
            ),
            BenchmarkCase(
                id="Z-T01",
                category="temporal",
                query="standup meeting notes",
                gold_path="nonexistent/does-not-exist-beta.md",
                score_method="exact",
            ),
            BenchmarkCase(
                id="Z-E01",
                category="entity",
                query="entity graph knowledge",
                gold_path="nonexistent/does-not-exist-gamma.md",
                score_method="exact",
            ),
            BenchmarkCase(
                id="Z-C01",
                category="conceptual",
                query="semantic similarity meaning",
                gold_path="nonexistent/does-not-exist-delta.md",
                score_method="exact",
            ),
            BenchmarkCase(
                id="Z-M01",
                category="multi_hop",
                query="multi hop planning",
                gold_path="nonexistent/does-not-exist-epsilon.md",
                score_method="exact",
            ),
            BenchmarkCase(
                id="Z-P01",
                category="procedural",
                query="deploy checklist steps",
                gold_path="nonexistent/does-not-exist-zeta.md",
                score_method="exact",
            ),
        ],
    )


@given("a benchmark suite with ndcg-scored cases")
def suite_ndcg() -> None:
    """Build a suite with NDCG-scored cases using gold_titles format."""
    _state["suite"] = BenchmarkSuite(
        meta={"name": "bdd-ndcg", "version": "1.0"},
        cases=[
            BenchmarkCase(
                id="N01",
                category="recall",
                query="hybrid retrieval architecture search BM25 vector",
                gold_path=None,
                score_method="ndcg",
                gold_titles=[
                    {"title": "Retrieval Architecture", "relevance": 2},
                    {"title": "BM25 Scoring", "relevance": 1},
                    {"title": "RRF Fusion", "relevance": 1},
                ],
            ),
            BenchmarkCase(
                id="N02",
                category="entity",
                query="entity graph Neo4j knowledge nodes mentions relationship",
                gold_path=None,
                score_method="ndcg",
                gold_titles=[
                    {"title": "Entity Graph", "relevance": 2},
                    {"title": "Alice Engineer", "relevance": 1},
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when(parsers.parse('the operator runs the benchmark with system "{system}"'))
def run_with_system(system: str) -> None:
    result = run_benchmark(_state["suite"], system=system)
    _state["result"] = result


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the result has a weighted_total score")
def result_has_weighted_total() -> None:
    result: BenchmarkResult = _state["result"]
    assert "weighted_total" in result.summary
    assert isinstance(result.summary["weighted_total"], float)


@then("the result has category_scores for each category")
def result_has_category_scores() -> None:
    result: BenchmarkResult = _state["result"]
    assert "category_scores" in result.summary
    assert isinstance(result.summary["category_scores"], dict)
    assert len(result.summary["category_scores"]) > 0


@then("the result has gate verdicts for phase1, phase2, and phase3")
def result_has_gate_verdicts() -> None:
    result: BenchmarkResult = _state["result"]
    gates = result.summary["gates"]
    for phase in ("phase1", "phase2", "phase3"):
        assert phase in gates, f"Missing gate verdict for {phase}"
        assert isinstance(gates[phase], bool)


@then("all phase gates pass")
def all_gates_pass() -> None:
    result: BenchmarkResult = _state["result"]
    gates = result.summary["gates"]
    for phase, passed in gates.items():
        assert passed, f"Gate {phase} failed — weighted_total {result.summary['weighted_total']:.4f}"


@then("the phase1 gate fails")
def phase1_gate_fails() -> None:
    result: BenchmarkResult = _state["result"]
    assert not result.summary["gates"]["phase1"], (
        f"Expected phase1 gate to fail but it passed with weighted_total {result.summary['weighted_total']:.4f}"
    )


@then("the weighted_total is below 0.62")
def weighted_total_below_phase1() -> None:
    result: BenchmarkResult = _state["result"]
    wt = result.summary["weighted_total"]
    assert wt < 0.62, f"Expected weighted_total < 0.62, got {wt:.4f}"


@then("the result includes ndcg_at_10")
def result_includes_ndcg() -> None:
    result: BenchmarkResult = _state["result"]
    assert result.summary["ndcg_at_10"] is not None
    assert isinstance(result.summary["ndcg_at_10"], float)
    assert result.summary["ndcg_at_10"] >= 0.0


@then("the result includes hit_rate_at_5")
def result_includes_hit_rate() -> None:
    result: BenchmarkResult = _state["result"]
    assert result.summary["hit_rate_at_5"] is not None
    assert isinstance(result.summary["hit_rate_at_5"], float)


@then("the result includes mrr_at_10")
def result_includes_mrr() -> None:
    result: BenchmarkResult = _state["result"]
    assert result.summary["mrr_at_10"] is not None
    assert isinstance(result.summary["mrr_at_10"], float)
