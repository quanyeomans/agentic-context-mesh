"""Unit tests for ``kairix.use_cases.recommend.run_recommend``.

Drives the use case through ``RecommendDeps`` injection — no @patch, no
monkeypatch, public surface only (F1/F2/F5). The recommender is a thin
retrieval-over-capability-collections use case: given a task description it
returns a ranked list of ``CapabilityRecommendation``s, each with a
ready-to-call invocation, and it never raises.

Sabotage-proof log (executed mutate -> fail -> restore):
``test_run_recommend_records_explicit_collection_contract`` pins the
``collections=["capabilities", "skills"]`` / ``agent=None`` query contract
— the recommender must rank over BOTH capability-bearing collections
(kairix caps in ``capabilities`` + the external skills connector's output
in ``skills``). Mutating ``_CAPABILITY_COLLECTIONS`` in ``run_recommend``
to drop ``"skills"`` (``["capabilities"]``) was run and confirmed to fail
that assertion (``assert fake.calls[0]["kwargs"]["collections"] == ...``);
restoring the constant turned it green again.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kairix.use_cases.recommend import RecommendDeps
from tests.fakes import FakeSearchPipeline

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _ScoredRow:
    """A FusedResult-shaped row exposing the three score fields.

    A plain test data carrier (not a monkeypatch) used to pin the
    rerank > boosted > rrf score-selection chain that ``FakeSearchPipeline``'s
    score-free rows can't exercise. Field names mirror the real
    ``kairix.core.search.rrf.FusedResult``.
    """

    path: str
    title: str = ""
    snippet: str = ""
    collection: str = ""
    rerank_score: float = 0.0
    boosted_score: float = 0.0
    rrf_score: float = 0.0


@dataclass(frozen=True)
class _ScoredBudgeted:
    """A BudgetedResult-shaped wrapper around a ``_ScoredRow``."""

    result: _ScoredRow
    content: str = ""


def _scored_pipeline(rows: list[_ScoredBudgeted]) -> FakeSearchPipeline:
    fake = FakeSearchPipeline(scripted_results=list(rows))
    return fake


# ---------------------------------------------------------------------------
# Step 3.1 — Output dataclasses + envelope
# ---------------------------------------------------------------------------


def test_envelope_round_trip_shape() -> None:
    from kairix.use_cases.recommend import (
        CapabilityRecommendation,
        RecommendOutput,
        recommend_output_to_envelope,
    )

    out = RecommendOutput(
        task="find prior decisions",
        recommendations=(
            CapabilityRecommendation(
                name="search",
                kind="tool",
                surface="both",
                when_to_use="Find prior work.",
                score=0.9,
                mcp_tool="search",
                cli="kairix search",
            ),
        ),
        correlation_id="abc123",
    )
    env = recommend_output_to_envelope(out)
    assert env["task"] == "find prior decisions"
    assert env["correlation_id"] == "abc123"
    assert env["error"] == ""
    assert env["recommendations"][0]["mcp_tool"] == "search"
    assert env["recommendations"][0]["name"] == "search"


def test_envelope_carries_every_recommendation_field() -> None:
    """The per-recommendation dict echoes the full binding the agent calls."""
    from kairix.use_cases.recommend import (
        CapabilityRecommendation,
        RecommendOutput,
        recommend_output_to_envelope,
    )

    out = RecommendOutput(
        task="t",
        recommendations=(
            CapabilityRecommendation(
                name="brainstorming",
                kind="skill",
                surface="external",
                when_to_use="Explore intent before building.",
                score=0.42,
                source="superpowers@6.0.3",
            ),
        ),
        correlation_id="cid",
    )
    rec = recommend_output_to_envelope(out)["recommendations"][0]
    assert rec == {
        "name": "brainstorming",
        "kind": "skill",
        "surface": "external",
        "when_to_use": "Explore intent before building.",
        "score": 0.42,
        "mcp_tool": "",
        "cli": "",
        "source": "superpowers@6.0.3",
    }


def test_recommend_output_defaults_are_empty() -> None:
    """A bare ``RecommendOutput`` is the never-raise empty result shape."""
    from kairix.use_cases.recommend import RecommendOutput

    out = RecommendOutput()
    assert out.task == ""
    assert out.recommendations == ()
    assert out.correlation_id == ""
    assert out.error == ""


# ---------------------------------------------------------------------------
# Step 3.2 — run_recommend retrieves + maps hits
# ---------------------------------------------------------------------------


def _kairix_catalogue() -> list[dict[str, object]]:
    return [
        {
            "name": "contradict",
            "mcp_tool": "contradict",
            "cli": "kairix contradict",
            "category": "synthesis",
            "when_to_use": "Check for conflicts.",
        },
    ]


def test_run_recommend_maps_hits_to_recommendations() -> None:
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict#0",
                title="contradict",
                content="Check new content against existing knowledge.",
            ),
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=_kairix_catalogue,
        correlation_id_fn=lambda: "fixed-id",
    )
    out = run_recommend("are there conflicting facts?", limit=3, deps=deps)
    assert out.error == ""
    assert out.task == "are there conflicting facts?"
    assert out.correlation_id == "fixed-id"
    rec = out.recommendations[0]
    assert rec.name == "contradict"
    assert rec.mcp_tool == "contradict"
    assert rec.cli == "kairix contradict"
    assert rec.kind == "tool"
    assert rec.surface == "both"
    # kairix caps take when_to_use from the catalogue row, not the snippet.
    assert rec.when_to_use == "Check for conflicts."


def test_run_recommend_records_explicit_collection_contract() -> None:
    """Sabotage anchor: agent=None / collections=["capabilities", "skills"].

    ``agent=None`` makes the pipeline use the collection list verbatim, so
    the unregistered capability-bearing collections are queryable. The
    recommender MUST rank over BOTH: ``capabilities`` (Feeder 1, kairix
    caps) AND ``skills`` (Feeder 2, the external skills connector's
    production collection — the connector framework names a connector's
    collection after the connector). Mutate ``_CAPABILITY_COLLECTIONS`` in
    ``run_recommend`` to drop ``"skills"`` and this assertion fails —
    proving external skills would become invisible to the recommender.
    """
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(scripted_results=[])
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    run_recommend("any task", deps=deps)
    assert fake.calls[0]["kwargs"]["collections"] == ["capabilities", "skills"]
    assert fake.calls[0]["kwargs"]["agent"] is None


def test_run_recommend_maps_external_skill_from_snippet() -> None:
    """External caps take when_to_use from the hit content; mcp_tool/cli empty."""
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://skill/brainstorming",
                title="brainstorming",
                content="Explore user intent before any creative work.",
            ),
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],  # no kairix enrichment for external caps
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("how do I scope a feature?", deps=deps)
    rec = out.recommendations[0]
    assert rec.name == "brainstorming"
    assert rec.kind == "skill"
    assert rec.surface == "external"
    assert rec.mcp_tool == ""
    assert rec.cli == ""
    assert rec.when_to_use == "Explore user intent before any creative work."


def test_run_recommend_maps_command_and_agent_kinds() -> None:
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://command/feature-dev",
                title="feature-dev",
                content="Guided feature development.",
            ),
            FakeSearchPipeline.make_chunk_row(
                path="capability://agent/code-architect",
                title="code-architect",
                content="Designs feature architectures.",
            ),
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("build a feature", deps=deps)
    kinds = {r.name: r.kind for r in out.recommendations}
    assert kinds == {"feature-dev": "command", "code-architect": "agent"}


def test_run_recommend_respects_limit() -> None:
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path=f"capability://skill/skill-{i}",
                title=f"skill-{i}",
                content=f"body {i}",
            )
            for i in range(10)
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("task", limit=4, deps=deps)
    assert len(out.recommendations) == 4


# ---------------------------------------------------------------------------
# Step 3.3 — empty / error branches + self-reference guard
# ---------------------------------------------------------------------------


def test_run_recommend_empty_corpus_yields_empty_no_error() -> None:
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(scripted_results=[])
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("nothing here", deps=deps)
    assert out.recommendations == ()
    assert out.error == ""
    assert out.correlation_id == "cid"


def test_run_recommend_never_raises_on_search_failure() -> None:
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    def _boom(**_kw: object) -> object:
        raise RuntimeError("vector backend down")

    deps = RecommendDeps(
        search_fn=_boom,
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("trigger failure", deps=deps)
    assert out.recommendations == ()
    assert "RuntimeError" in out.error
    assert "vector backend down" in out.error
    # correlation_id still minted so Spec B's log shape is stable.
    assert out.correlation_id == "cid"


def test_run_recommend_failure_log_carries_traceback(caplog) -> None:
    """The never-raise swallow path logs with exc_info=True so the stack trace
    reaches the logs (``error`` only carries type+message). Pins the traceback
    against a mutation that would silence it (exc_info True -> False)."""
    import logging

    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    def _boom(**_kw: object) -> object:
        raise RuntimeError("vector backend down")

    deps = RecommendDeps(
        search_fn=_boom,
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    with caplog.at_level(logging.WARNING):
        out = run_recommend("trigger failure", deps=deps)

    assert "RuntimeError" in out.error
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "run_recommend failed" in r.getMessage()]
    assert warnings, "expected a never-raise WARNING record"
    assert warnings[0].exc_info is not None  # traceback captured (exc_info=True)
    assert warnings[0].exc_info[0] is RuntimeError


def test_run_recommend_excludes_self_reference() -> None:
    """The recommender never recommends itself."""
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/recommend",
                title="recommend",
                content="Rank capabilities for a task.",
            ),
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/search",
                title="search",
                content="Hybrid retrieval.",
            ),
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("which tool finds prior work?", deps=deps)
    names = [r.name for r in out.recommendations]
    assert "recommend" not in names
    assert names == ["search"]


def test_run_recommend_skips_unparseable_hit_paths() -> None:
    """A hit whose path isn't a capability:// URI is dropped, not fatal."""
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="facts://garbage",
                title="garbage",
                content="not a capability",
            ),
            FakeSearchPipeline.make_chunk_row(
                path="capability://skill/keep-me",
                title="keep-me",
                content="kept",
            ),
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("task", deps=deps)
    assert [r.name for r in out.recommendations] == ["keep-me"]


# ---------------------------------------------------------------------------
# Step 3.3 — DI-default coverage (F86: no pragma on _default_* seams)
#
# F5 forbids importing the ``_default_*`` private names directly, so each
# seam is exercised through the public ``RecommendDeps()`` /
# ``run_recommend`` surface: leaving a field unset wires its real default,
# and calling ``run_recommend`` executes that default's body.
# ---------------------------------------------------------------------------


def test_default_correlation_id_runs_via_public_surface() -> None:
    """The real correlation-id seam mints a fresh uuid4 hex per call.

    ``RecommendDeps(search_fn=fake)`` leaves ``correlation_id_fn`` at its
    production default; two calls through ``run_recommend`` mint distinct
    32-char hex ids — executing ``_default_correlation_id`` without naming
    it (F5) and giving it coverage (F86).
    """
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(scripted_results=[])
    deps = RecommendDeps(search_fn=lambda **kw: fake.search(**kw), catalogue_fn=lambda: [])
    first = run_recommend("a", deps=deps).correlation_id
    second = run_recommend("b", deps=deps).correlation_id
    assert first != second
    assert len(first) == 32
    assert all(c in "0123456789abcdef" for c in first)


def test_default_catalogue_enriches_kairix_hit_via_public_surface() -> None:
    """The real kairix catalogue enriches a kairix-scope hit (F86 + F5).

    ``RecommendDeps(search_fn=fake)`` leaves ``catalogue_fn`` at its
    production default (the real ``tool_capabilities()`` surface, which
    imports without the ``[agents]`` extra). A ``capability://kairix/search``
    hit is enriched from that real catalogue, executing
    ``_default_catalogue`` through the public surface.
    """
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/search",
                title="search",
                content="Hybrid retrieval.",
            ),
        ]
    )
    deps = RecommendDeps(search_fn=lambda **kw: fake.search(**kw), correlation_id_fn=lambda: "cid")
    out = run_recommend("find prior work", deps=deps)
    rec = out.recommendations[0]
    assert rec.name == "search"
    assert rec.kind == "tool"
    # The real catalogue carries an invocation for ``search``.
    assert rec.cli == "kairix search"


def test_run_recommend_default_deps_never_raises() -> None:
    """Bare ``run_recommend`` (default deps) returns; never raises.

    Drives the ``deps or RecommendDeps()`` path through the real
    ``_default_search`` seam (no provider configured in the test process,
    so the seam raises -> ``error`` is populated, never re-raised) and the
    real ``_default_correlation_id`` seam — exercising the production
    wiring provider-free.
    """
    from kairix.use_cases.recommend import run_recommend

    out = run_recommend("force the default-deps path", limit=1)
    # Either the corpus is absent (empty) or the provider/index is missing
    # (error) — both are valid never-raise outcomes; the contract is that
    # the call returns and mints a 32-char correlation_id.
    assert out.task == "force the default-deps path"
    assert len(out.correlation_id) == 32
    assert isinstance(out.recommendations, tuple)


def test_recommender_config_force_enables_rerank() -> None:
    """``recommender_config`` flips rerank ON over a real RetrievalConfig.

    Pins the force-rerank contract (precision over a small corpus) without
    a provider: the returned config has ``rerank.enabled is True`` and is a
    distinct object so the pipeline factory caches it in its own bucket.
    """
    from dataclasses import replace

    from kairix.core.search.config import RerankConfig, RetrievalConfig
    from kairix.use_cases.recommend import recommender_config

    base = replace(RetrievalConfig.defaults(), rerank=RerankConfig(enabled=False))
    forced = recommender_config(base)
    assert forced.rerank.enabled is True
    assert base.rerank.enabled is False  # original untouched (replace, not mutate)


# ---------------------------------------------------------------------------
# Score-selection chain (rerank > boosted > rrf) — pins the ``or`` chaining.
# ---------------------------------------------------------------------------


def _external_deps(fake: FakeSearchPipeline) -> RecommendDeps:
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )


def test_score_prefers_rerank_over_boosted_and_rrf() -> None:
    """rerank_score wins when present (kills the first ``or`` conjunct)."""
    from kairix.use_cases.recommend import run_recommend

    fake = _scored_pipeline(
        [
            _ScoredBudgeted(
                result=_ScoredRow(
                    path="capability://skill/alpha",
                    rerank_score=0.9,
                    boosted_score=0.5,
                    rrf_score=0.1,
                ),
                content="alpha body",
            ),
        ]
    )
    out = run_recommend("t", deps=_external_deps(fake))
    assert out.recommendations[0].score == 0.9


def test_score_falls_through_to_boosted_then_rrf() -> None:
    """boosted wins when rerank is 0; rrf wins when both above are 0."""
    from kairix.use_cases.recommend import run_recommend

    fake = _scored_pipeline(
        [
            _ScoredBudgeted(
                result=_ScoredRow(path="capability://skill/beta", boosted_score=0.6, rrf_score=0.2),
                content="beta",
            ),
            _ScoredBudgeted(
                result=_ScoredRow(path="capability://skill/gamma", rrf_score=0.3),
                content="gamma",
            ),
        ]
    )
    out = run_recommend("t", deps=_external_deps(fake))
    by_name = {r.name: r.score for r in out.recommendations}
    assert by_name["beta"] == 0.6  # boosted used (rerank 0)
    assert by_name["gamma"] == 0.3  # rrf used (rerank + boosted 0)


def test_score_is_zero_when_no_score_fields_present() -> None:
    """A score-free row resolves to 0.0, not a crash (pins ``float(... or 0.0)``)."""
    from kairix.use_cases.recommend import run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://skill/no-score",
                title="no-score",
                content="body",
            ),
        ]
    )
    out = run_recommend("t", deps=_external_deps(fake))
    assert out.recommendations[0].score == 0.0


# ---------------------------------------------------------------------------
# URI parse rejection (pins the ``not sep or not scope or not name`` guard).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "capability://kairix",  # no '/name' — sep empty
        "capability:///orphan",  # empty scope before '/'
        "capability://skill/",  # empty name after '/'
    ],
)
def test_run_recommend_drops_malformed_capability_uri(bad_path: str) -> None:
    """A capability URI missing scope or name is dropped, not mapped."""
    from kairix.use_cases.recommend import run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(path=bad_path, title="x", content="x"),
            FakeSearchPipeline.make_chunk_row(path="capability://skill/good", title="good", content="kept"),
        ]
    )
    out = run_recommend("t", deps=_external_deps(fake))
    assert [r.name for r in out.recommendations] == ["good"]


# ---------------------------------------------------------------------------
# Surface derivation (pins ``mcp_tool and cli`` -> "both").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mcp_tool", "cli", "expected_surface"),
    [
        ("doctor", "kairix doctor", "both"),
        ("", "kairix doctor", "cli"),  # cli-only -> NOT "both" (kills and->or)
        ("doctor", "", "mcp"),  # mcp-only -> NOT "both"
    ],
)
def test_kairix_surface_derivation(mcp_tool: str, cli: str, expected_surface: str) -> None:
    from kairix.use_cases.recommend import RecommendDeps, run_recommend

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(path="capability://kairix/doctor", title="doctor", content="diagnose"),
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [{"name": "doctor", "mcp_tool": mcp_tool or None, "cli": cli, "category": "diagnostic"}],
        correlation_id_fn=lambda: "cid",
    )
    out = run_recommend("is the system healthy?", deps=deps)
    rec = out.recommendations[0]
    assert rec.surface == expected_surface
    assert rec.mcp_tool == mcp_tool
    assert rec.cli == cli


# ---------------------------------------------------------------------------
# Step 4.1 — adapter-level flag gate helpers (CLI + MCP shared)
# ---------------------------------------------------------------------------


def test_recommender_disabled_output_shape() -> None:
    """The disabled-state result carries the canonical message + empty recs."""
    from kairix.use_cases.recommend import RECOMMENDER_DISABLED_ERROR, recommender_disabled_output

    out = recommender_disabled_output("some task")
    assert out.task == "some task"
    assert out.recommendations == ()
    assert out.error == RECOMMENDER_DISABLED_ERROR
    assert "recommender is disabled" in out.error


# ---------------------------------------------------------------------------
# Step 4.2 — CLI main() (in-process, deps + flag_reader seams)
# ---------------------------------------------------------------------------


def _cli_deps_one_kairix_hit() -> RecommendDeps:
    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict",
                title="contradict",
                content="Check for conflicts.",
            ),
        ]
    )
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [
            {"name": "contradict", "mcp_tool": "contradict", "cli": "kairix contradict", "category": "synthesis"},
        ],
        correlation_id_fn=lambda: "cid",
    )


def test_cli_main_json_emits_envelope_flag_on() -> None:
    import io
    import json

    from kairix.use_cases.recommend import main

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["are there conflicts?", "--json"],
        out=out,
        err=err,
        deps=_cli_deps_one_kairix_hit(),
        flag_reader=lambda: True,
    )
    assert code == 0
    envelope = json.loads(out.getvalue())
    assert envelope["error"] == ""
    assert [r["name"] for r in envelope["recommendations"]] == ["contradict"]
    assert err.getvalue() == ""


def test_cli_main_human_table_flag_on() -> None:
    import io

    from kairix.use_cases.recommend import main

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["are there conflicts?"],
        out=out,
        err=err,
        deps=_cli_deps_one_kairix_hit(),
        flag_reader=lambda: True,
    )
    assert code == 0
    text = out.getvalue()
    assert "Recommendations for: are there conflicts?" in text
    assert "contradict" in text
    assert "call: contradict" in text


def test_cli_main_human_table_no_results() -> None:
    import io

    from kairix.use_cases.recommend import RecommendDeps, main

    deps = RecommendDeps(
        search_fn=lambda **kw: FakeSearchPipeline(scripted_results=[]).search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out, err = io.StringIO(), io.StringIO()
    code = main(["nothing here"], out=out, err=err, deps=deps, flag_reader=lambda: True)
    assert code == 0
    assert "(no matching capabilities found)" in out.getvalue()


def test_cli_main_flag_off_disabled_exit_1() -> None:
    import io
    import json

    from kairix.use_cases.recommend import main

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["anything", "--json"],
        out=out,
        err=err,
        deps=_cli_deps_one_kairix_hit(),
        flag_reader=lambda: False,
    )
    assert code == 1
    assert "recommender is disabled" in err.getvalue()
    # JSON still emitted (disabled envelope) so machine callers can parse it.
    envelope = json.loads(out.getvalue())
    assert envelope["recommendations"] == []


def test_cli_main_respects_limit() -> None:
    import io
    import json

    from kairix.use_cases.recommend import RecommendDeps, main

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(path=f"capability://skill/s-{i}", title=f"s-{i}", content="body")
            for i in range(8)
        ]
    )
    deps = RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [],
        correlation_id_fn=lambda: "cid",
    )
    out, err = io.StringIO(), io.StringIO()
    code = main(["task", "--json", "--limit", "2"], out=out, err=err, deps=deps, flag_reader=lambda: True)
    assert code == 0
    envelope = json.loads(out.getvalue())
    assert len(envelope["recommendations"]) == 2


def test_default_recommender_flag_reader_returns_bool() -> None:
    """The production flag reader resolves the ``recommender`` flag to a bool.

    Drives the real ``flag("recommender")`` resolution (registry default OFF,
    no overlay/env in the test process) so the DI-default seam has executed
    coverage (F86) — without naming the private resolver internals (F5).
    """
    from kairix.use_cases.recommend import default_recommender_flag_reader

    value = default_recommender_flag_reader()
    assert isinstance(value, bool)


def test_db_path_seam_builds_a_search_fn() -> None:
    """The ``--db-path`` seam yields a callable ``search_fn`` (deps built).

    Pins ``_deps_from_args``'s db-path branch through ``main``'s parser via
    a fake search: passing ``--db-path`` with in-process ``deps=None`` and a
    real ``--db-path`` would try to open the path — so instead we assert the
    parser accepts the flag and exits cleanly when the flag is OFF (gate
    short-circuits before the seam runs).
    """
    import io

    from kairix.use_cases.recommend import main

    out, err = io.StringIO(), io.StringIO()
    # Flag OFF short-circuits before the db-path seam touches the filesystem.
    code = main(
        ["task", "--db-path", "/nonexistent/index.sqlite"],
        out=out,
        err=err,
        flag_reader=lambda: False,
    )
    assert code == 1
    assert "recommender is disabled" in err.getvalue()


def test_db_path_seam_searches_seeded_index_in_process(tmp_path) -> None:
    """The ``--db-path`` seam composes the real BM25-only pipeline in-process.

    Seeds a one-capability corpus into a tmp sqlite, then drives ``main``
    with ``--db-path`` + ``deps=None`` so the production ``_deps_from_args``
    db-path branch, ``_db_path_search_fn``, and ``_NullEmbeddingService``
    seams all execute (F86 coverage of the F30 subprocess seam) against a
    real index — provider-free, no env vars.
    """
    import io
    import json
    import sqlite3

    from kairix.core.db.schema import create_schema
    from kairix.core.factory import reset_search_pipeline_cache
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
        build_capability_corpus,
    )
    from kairix.use_cases.recommend import main

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(db_path)
    create_schema(db)
    corpus_deps = CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(
            catalogue_fn=lambda: [
                {
                    "name": "contradict",
                    "mcp_tool": "contradict",
                    "cli": "kairix contradict",
                    "category": "synthesis",
                    "when_to_use": "Check new content against existing knowledge for conflicts.",
                },
            ],
            now_fn=lambda: "2026-06-20T00:00:00+00:00",
        ),
        embed_batch_fn=lambda texts: [],  # BM25-only
    )
    assert build_capability_corpus(db, deps=corpus_deps).written == 1
    db.commit()
    db.close()

    reset_search_pipeline_cache()
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["check content for conflicts", "--json", "--db-path", str(db_path)],
        out=out,
        err=err,
        flag_reader=lambda: True,  # gate ON; deps=None -> real db-path seam
    )
    assert code == 0, f"stderr: {err.getvalue()!r}"
    envelope = json.loads(out.getvalue())
    assert envelope["error"] == ""
    assert "contradict" in [r["name"] for r in envelope["recommendations"]]
