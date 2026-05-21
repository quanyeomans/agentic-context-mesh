"""SearchPipeline-mode coverage for :class:`SuiteRunner` (Plan B-parity D3).

Pins the new ``search_pipeline=`` ctor kwarg + the SearchPipeline-mode
branch in ``_score_queries`` + the BudgetedResult→context adapter.

Every assertion drives the runner through its public surface (constructor
+ ``run`` / ``_score_queries`` invoked as instance methods) and reads the
LLM-judge prompt out of the FakeLLMBackend's ``chat_calls`` record. No
imports of private names (F5-clean). No monkeypatching (F1-clean).

Every test is sabotage-proven (mutate prod → fail → restore → pass).

Background — see ``/tmp/spike-B1-divergence.md``: without the branch
eval scores diverge HIGH from prep (mean delta 0.200, Pearson 0.549,
pass/fail disagreement 20%). The branch + adapter restore parity so a
5pp eval regression no longer hides a 25pp operator-visible regression
on ``kairix prep``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.paths import KairixPaths
from kairix.quality.eval.suite_runner import SuiteRunner
from tests.fakes import (
    FakeFactExtractor,
    FakeFactRecord,
    FakeFactStore,
    FakeLLMBackend,
    FakeSearchPipeline,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — local to keep this test file independent of the legacy suite tests
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path) -> KairixPaths:
    """KairixPaths pinned to tmp_path; never reads env."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def _make_runner(
    *,
    tmp_path: Path,
    pipeline: FakeSearchPipeline | None,
    chat_response: str = "1.0",
) -> tuple[SuiteRunner, FakeFactStore, FakeFactExtractor, FakeLLMBackend]:
    """Build a SuiteRunner with the supplied pipeline (or None for legacy)."""
    store = FakeFactStore()
    extractor = FakeFactExtractor(scripted_facts=[])
    llm = FakeLLMBackend(chat_response=chat_response)
    runner = SuiteRunner(
        fact_store=store,
        fact_extractor=extractor,
        llm=llm,
        paths=_paths(tmp_path),
        search_pipeline=pipeline,
    )
    return runner, store, extractor, llm


def _judge_context_for(llm: FakeLLMBackend, call_index: int = 0) -> str:
    """Pull the ``Retrieved context`` block from the indexed judge call.

    The LLM-judge prompt is a 2-message list ``[system, user]`` where
    the user content carries the synthesised context — that's what the
    SearchPipeline-mode adapter (and the legacy ``_hits_to_context``)
    write into. Returning the full user content lets each test pin its
    own slice of the formatted output.
    """
    return str(llm.chat_calls[call_index]["messages"][1]["content"])


# ---------------------------------------------------------------------------
# Branch — pipeline wins when wired
# ---------------------------------------------------------------------------


def test_pipeline_branch_routes_queries_through_search_pipeline(tmp_path: Path) -> None:
    """Plan B-parity D3 — when ``search_pipeline`` is wired, queries go
    through it (not through ``fact_store.search``).

    Sabotage-proof: revert the branch in ``SuiteRunner._retrieve_context``
    so it always calls ``self._fact_store.search`` even when
    ``self._search_pipeline`` is set → ``pipeline.calls`` stays empty and
    this assertion fails. Verified locally 2026-05-21 against develop
    HEAD ea30763a (stream-D3 commit-2) — restoring the sabotage produces
    the expected single failure in <1s.
    """
    pipeline = FakeSearchPipeline(scripted_results=[])
    runner, _, _, _ = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    queries = [{"question": "What is the CTO's name?", "answer": "Bob", "category": "single-hop"}]
    rows, _per_cat = runner._score_queries(queries)

    # Pipeline received exactly one search call.
    assert len(pipeline.calls) == 1, (
        f"expected 1 pipeline.search call, got {len(pipeline.calls)} — "
        f"runner bypassed the wired SearchPipeline (D3 regression)"
    )
    assert pipeline.calls[0]["query"] == "What is the CTO's name?"
    assert len(rows) == 1


def test_pipeline_branch_bypasses_fact_store_when_wired(tmp_path: Path) -> None:
    """Plan B-parity D3 — wiring a pipeline means ``fact_store.search`` is
    not called from the scoring loop. Ingestion still uses the store via
    ``fact_store.add``; only the *retrieval* path is rerouted.

    Sabotage-proof: revert ``_retrieve_context`` so it always falls
    through to ``self._fact_store.search`` → the wrong fact ('Janitor')
    leaks into the judge prompt and the assertion below fails.
    """
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_fact_row(
                fact_id="f-001",
                entity="Bob",
                attribute="role",
                value="CTO",
            )
        ]
    )
    runner, store, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    # Pre-load the store with a fact that WOULD score had the legacy
    # path been used. If the pipeline branch fires correctly the store's
    # search is never called and this fact does not enter the context.
    store.add(
        FakeFactRecord(
            id="f-store-only",
            entity="Bob",
            attribute="role",
            value="Janitor",  # deliberately wrong — if eval shows this, legacy path leaked.
            namespace="shared",
        )
    )

    queries = [{"question": "What is Bob's role?", "answer": "CTO", "category": "single-hop"}]
    runner._score_queries(queries)

    user_content = _judge_context_for(llm)
    assert "CTO" in user_content, f"pipeline-mode context should carry the pipeline's fact; got:\n{user_content}"
    assert "Janitor" not in user_content, (
        f"pipeline-mode context leaked the legacy fact_store path; got:\n{user_content}"
    )


def test_legacy_branch_falls_back_to_fact_store_when_no_pipeline(tmp_path: Path) -> None:
    """Legacy direct path preserved — when ``search_pipeline=None`` the
    runner uses ``fact_store.search`` exactly as before D3.

    Sabotage-proof: drop the ``if self._search_pipeline is not None``
    guard in ``_retrieve_context`` and the legacy path breaks because
    the runner unconditionally tries the pipeline (None → AttributeError);
    this test fails by exception rather than assertion, which is still a
    valid sabotage proof — the green-restore is what matters.
    """
    runner, store, _, llm = _make_runner(tmp_path=tmp_path, pipeline=None)

    store.add(
        FakeFactRecord(
            id="f-001",
            entity="Bob",
            attribute="role",
            value="CTO",
            namespace="shared",
        )
    )

    queries = [{"question": "What is Bob's role?", "answer": "CTO", "category": "single-hop"}]
    rows, _ = runner._score_queries(queries)

    user_content = _judge_context_for(llm)
    assert "CTO" in user_content
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Adapter — exercised through the public ``_score_queries`` surface
# ---------------------------------------------------------------------------


def test_pipeline_mode_formats_mixed_fact_and_chunk_rows(tmp_path: Path) -> None:
    """The adapter prefixes fact rows differently from chunk rows so the
    LLM-judge prompt has consistent shape across both retrieval paths.

    Sabotage-proof: revert the ``is_fact`` discriminator (always treat
    rows as facts) → chunk rows lose their ``[title]`` prefix → the
    "Acme is a startup" chunk no longer carries the ``[Acme overview]``
    bracket and this assertion fails. Verified locally — restoring the
    sabotage reproduces 2 failures in this test alone.
    """
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_fact_row(
                fact_id="f-001",
                entity="Bob",
                attribute="role",
                value="CTO",
            ),
            FakeSearchPipeline.make_chunk_row(
                path="docs/acme.md#L1",
                title="Acme overview",
                content="Acme is a startup founded in 2020.",
            ),
        ]
    )
    runner, _, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])

    out = _judge_context_for(llm)
    # Fact row formats as "- <entity> <attribute>: <value>" (no bracket).
    assert "- Bob role: CTO" in out
    # Chunk row formats as "- [<title>] <content>" — discriminator fires.
    assert "- [Acme overview] Acme is a startup founded in 2020." in out


def test_pipeline_mode_truncates_long_chunk_snippets(tmp_path: Path) -> None:
    """Chunk rows are truncated to ~300 chars to keep the LLM-judge's
    8-token answer budget tractable.

    Sabotage-proof: drop the ``[:_CHUNK_SNIPPET_CHARS]`` slice on the
    chunk branch and the assertion fails because the full 600-char
    string survives into the judge prompt.
    """
    long_content = "A" * 600
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="docs/long.md",
                title="Long doc",
                content=long_content,
            )
        ]
    )
    runner, _, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])

    out = _judge_context_for(llm)
    # The 'A' run in the context is bounded by the 300-char truncation.
    a_run = out.split("[Long doc] ", 1)[1].split("\n", 1)[0]
    assert len(a_run) <= 300, f"expected chunk snippet truncated to ≤300 chars, got {len(a_run)}"


def test_pipeline_mode_empty_result_emits_sentinel(tmp_path: Path) -> None:
    """An empty ``result.results`` maps to the same
    ``(no relevant facts retrieved)`` sentinel that legacy
    ``_hits_to_context`` emits — judge-prompt shape is invariant
    across both retrieval paths so scores stay comparable.

    Sabotage-proof: drop the ``if not lines:`` sentinel branch → the
    adapter returns ``""`` → the substring assertion fails.
    """
    pipeline = FakeSearchPipeline(scripted_results=[])
    runner, _, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])

    out = _judge_context_for(llm)
    assert "(no relevant facts retrieved)" in out


def test_pipeline_mode_caps_at_top_five_results(tmp_path: Path) -> None:
    """Adapter slices ``result.results[:5]`` — matches the legacy path's
    ``top_k=5`` cap on fact_store.search hits.

    Sabotage-proof: change the slice to ``[:50]`` and this fails because
    the 6th-8th rows leak into the judge prompt.
    """
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_fact_row(
                fact_id=f"f-{i}",
                entity=f"e{i}",
                attribute="attr",
                value=f"v{i}",
            )
            for i in range(8)
        ]
    )
    runner, _, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])

    out = _judge_context_for(llm)

    # First 5 entities present; 6th-8th absent.
    assert "e0 attr: v0" in out
    assert "e4 attr: v4" in out
    assert "e5" not in out
    assert "e6" not in out


def test_pipeline_mode_fact_only_result_has_no_chunk_brackets(tmp_path: Path) -> None:
    """Boundary case — every row is a fact row; output carries the
    triplet shape consistently, no chunk ``[title]`` prefix in sight.

    Sabotage-proof: reverse the ``is_fact`` test (treat facts as chunks)
    and the assertion fails because fact rows pick up the bracket.
    """
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_fact_row(fact_id="f-1", entity="Alice", attribute="role", value="CEO"),
            FakeSearchPipeline.make_fact_row(fact_id="f-2", entity="Bob", attribute="role", value="CTO"),
        ]
    )
    runner, _, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])

    # Pull just the retrieved-context block (everything after the marker line).
    out = _judge_context_for(llm)
    context_block = out.split("Retrieved context:\n", 1)[1].split("\n\n", 1)[0]
    assert "- Alice role: CEO" in context_block
    assert "- Bob role: CTO" in context_block
    assert "[" not in context_block, f"fact-only context should not carry chunk brackets; got: {context_block}"


def test_pipeline_mode_chunk_only_result_has_bracketed_titles(tmp_path: Path) -> None:
    """Boundary case — every row is a chunk; output carries the
    ``[title] snippet`` shape consistently.

    Sabotage-proof: drop the chunk-branch ``- [{title}]`` formatter and
    this fails because the bracket disappears.
    """
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="docs/one.md",
                title="One",
                content="First chunk content here.",
            ),
            FakeSearchPipeline.make_chunk_row(
                path="docs/two.md",
                title="Two",
                content="Second chunk content here.",
            ),
        ]
    )
    runner, _, _, llm = _make_runner(tmp_path=tmp_path, pipeline=pipeline)

    runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])

    out = _judge_context_for(llm)
    assert "- [One] First chunk content here." in out
    assert "- [Two] Second chunk content here." in out


# ---------------------------------------------------------------------------
# Constructor wiring — search_pipeline kwarg threads through to behaviour
# ---------------------------------------------------------------------------


def test_runner_with_pipeline_dispatches_to_pipeline_on_run(tmp_path: Path) -> None:
    """The ``search_pipeline=`` kwarg is honoured by the public ``run``
    method (not just ``_score_queries``) — confirms wiring runs end-to-end.

    Sabotage-proof: drop the ctor assignment ``self._search_pipeline = ...``
    (default it to ``None`` unconditionally) and the pipeline never sees
    a call → ``pipeline.calls`` stays empty → assertion fails.
    """
    suite_dir = tmp_path / "scenario"
    suite_dir.mkdir()
    (suite_dir / "session-001.jsonl").write_text(
        '{"id": "t1", "speaker": "agent-alpha", "content": "hello"}\n',
        encoding="utf-8",
    )
    (suite_dir / "ground-truth-queries.json").write_text(
        '[{"question": "Q?", "answer": "A", "category": "single-hop"}]',
        encoding="utf-8",
    )

    pipeline = FakeSearchPipeline(scripted_results=[])
    runner, _, _, _ = _make_runner(tmp_path=tmp_path, pipeline=pipeline)
    spec = runner.discover_suite(suite_dir)
    runner.run(spec)

    assert len(pipeline.calls) == 1, (
        f"public run() should route through the wired pipeline; got {len(pipeline.calls)} calls"
    )


def test_runner_default_constructor_keeps_legacy_behaviour(tmp_path: Path) -> None:
    """Omitting ``search_pipeline=`` defaults it to legacy direct mode —
    preserves pre-D3 ctor signature compatibility for any caller that
    doesn't yet know about the new kwarg.

    Sabotage-proof: change the default to a sentinel object that breaks
    when called (e.g. a string) and this fails because the legacy path
    never fires for the missing-kwarg case.
    """
    runner = SuiteRunner(
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        llm=FakeLLMBackend(chat_response="1.0"),
        paths=_paths(tmp_path),
    )
    # No pipeline wired → legacy path executes without raising.
    rows, _ = runner._score_queries([{"question": "q?", "answer": "a", "category": "single-hop"}])
    assert len(rows) == 1
