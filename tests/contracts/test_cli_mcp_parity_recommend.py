"""Contract: CLI ↔ MCP parity for the ``recommend`` capability (Spec A).

The recommender ships one use case (``run_recommend``) behind two thin
adapters: the CLI ``kairix recommend`` (``kairix.use_cases.recommend.main``,
``--json``) and the MCP ``recommend_capabilities`` tool
(``kairix.agents.mcp.server.tool_recommend``). This contract proves the two
adapters return the SAME recommendation envelope for the same task — the
CLI↔MCP parity invariant.

Both adapters are driven through their public surfaces with the SAME
injected ``RecommendDeps`` (a ``FakeSearchPipeline`` + fake catalogue) and
the flag forced ON via the ``flag_reader`` seam, so the comparison isolates
the adapter wiring, not the retrieval backend (F1/F2/F5-clean — no @patch,
no env vars, public surface only).
"""

from __future__ import annotations

import io
import json

import pytest

from kairix.agents.mcp.server import tool_recommend
from kairix.use_cases.recommend import RecommendDeps
from kairix.use_cases.recommend import main as recommend_main
from tests.fakes import FakeSearchPipeline

pytestmark = pytest.mark.contract

_TASK = "I need to check this against what we already know"


def _shared_deps() -> RecommendDeps:
    """Deps both adapters share — one kairix-tool hit + its catalogue row."""
    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict",
                title="contradict",
                content="Check new content against existing knowledge for conflicts.",
            ),
        ]
    )
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [
            {
                "name": "contradict",
                "mcp_tool": "contradict",
                "cli": "kairix contradict",
                "category": "synthesis",
                "when_to_use": "Check for conflicts.",
            },
        ],
        correlation_id_fn=lambda: "fixed-id",
    )


def _cli_envelope() -> dict:
    out, err = io.StringIO(), io.StringIO()
    code = recommend_main(
        [_TASK, "--json"],
        out=out,
        err=err,
        deps=_shared_deps(),
        flag_reader=lambda: True,
    )
    assert code == 0, f"CLI exited {code}; stderr={err.getvalue()!r}"
    return json.loads(out.getvalue())


def _mcp_envelope() -> dict:
    return tool_recommend(task=_TASK, deps=_shared_deps(), flag_reader=lambda: True)


def test_cli_and_mcp_return_equivalent_recommendations() -> None:
    """Same task → byte-identical recommendation envelopes through both adapters."""
    cli = _cli_envelope()
    mcp = _mcp_envelope()

    assert cli == mcp, f"CLI and MCP envelopes diverged:\nCLI={cli!r}\nMCP={mcp!r}"
    # And the shared content is a real recommendation, not an empty match.
    assert cli["error"] == ""
    assert [r["name"] for r in cli["recommendations"]] == ["contradict"]


def test_cli_and_mcp_agree_when_disabled() -> None:
    """Flag OFF → both adapters return the same disabled envelope."""
    out, err = io.StringIO(), io.StringIO()
    recommend_main([_TASK, "--json"], out=out, err=err, deps=_shared_deps(), flag_reader=lambda: False)
    cli = json.loads(out.getvalue())
    mcp = tool_recommend(task=_TASK, deps=_shared_deps(), flag_reader=lambda: False)

    assert cli == mcp
    assert cli["recommendations"] == []
    assert "recommender is disabled" in cli["error"]


def test_cli_main_calls_run_recommend() -> None:
    """The CLI adapter delegates to the shared ``run_recommend`` use case."""
    import inspect

    from kairix.use_cases import recommend as uc

    src = inspect.getsource(uc.main)
    assert "run_recommend(" in src


def test_mcp_tool_recommend_calls_run_recommend() -> None:
    """The MCP adapter delegates to the shared ``run_recommend`` use case."""
    import inspect

    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_recommend)
    assert "run_recommend(" in src
    assert "from kairix.use_cases.recommend import" in src


def test_kairix_recommend_command_is_registered() -> None:
    from kairix.cli import COMMANDS

    assert "recommend" in COMMANDS
    assert COMMANDS["recommend"][0] == "kairix.use_cases.recommend"


def test_recommend_capability_row_is_in_the_catalogue() -> None:
    """The recommender is itself discoverable via ``tool_capabilities()``.

    Per design §4.2, a ``_cap(name="recommend", ...)`` row makes the
    recommender callable-by-discovery (an agent introspecting the catalogue
    finds it) AND feeds Feeder 1's corpus build (which reads
    ``tool_capabilities()``). The row carries the canonical MCP tool name +
    CLI invocation + a when_to_use trigger.

    Sabotage anchor (executed mutate -> fail -> restore): removing the
    ``_cap(name="recommend", ...)`` row from ``tool_capabilities()`` makes
    this test fail on the ``"recommend" in by_name`` assertion.
    """
    from kairix.agents.mcp.server import RECOMMEND_CAPABILITIES_TOOL_NAME, tool_capabilities

    by_name = {c["name"]: c for c in tool_capabilities()["capabilities"]}
    assert "recommend" in by_name, "recommender must be discoverable in the capability catalogue"
    row = by_name["recommend"]
    assert row["mcp_tool"] == RECOMMEND_CAPABILITIES_TOOL_NAME
    assert row["cli"] == "kairix recommend"
    assert row.get("when_to_use", "").strip(), "recommend row must advertise when to reach for it"


# ---------------------------------------------------------------------------
# Behaviour pins (kill the diff-scoped mutation_parity mutants). These live
# in this contract file because mutation_parity runs the *combined* impacted
# set capped at the first-40 sorted test files — and tests/contracts/ sorts
# inside that window while tests/use_cases/ + tests/knowledge/ do not.
# ---------------------------------------------------------------------------


def test_read_only_db_search_config_is_bm25_only() -> None:
    """The ``--db-path`` seam config is BM25-only (``skip_vector`` True) + rerank on.

    Mutation anchor: flipping ``skip_vector=True`` to ``False`` in
    ``read_only_db_search_config`` makes the read-only seam attempt the
    vector leg against a missing provider. This pins the constant.
    """
    from kairix.use_cases.recommend import read_only_db_search_config

    cfg = read_only_db_search_config()
    assert cfg.skip_vector is True, "the read-only --db-path seam must be BM25-only (no provider)"
    assert cfg.rerank.enabled is True, "rerank stays force-on for the recommender"


def test_cli_human_invocation_prefers_cli_when_mcp_tool_empty() -> None:
    """``_format_human`` picks ``cli`` when ``mcp_tool`` is empty (``or`` chain).

    Mutation anchor: ``rec.mcp_tool or rec.cli or rec.name`` with ``or`` ->
    ``and`` would resolve to ``""`` (first falsy) for a CLI-only cap and the
    ``call:`` line would lose the invocation. Drive a CLI-only hit through
    the human (non-JSON) CLI output and assert the cli invocation appears.
    """
    import io

    from kairix.use_cases.recommend import RecommendDeps
    from kairix.use_cases.recommend import main as recommend_main

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(path="capability://kairix/doctor", title="doctor", content="diagnose"),
        ]
    )
    deps = RecommendDeps(
        # CLI-only cap: mcp_tool empty, cli set — the `or` chain must pick cli.
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [{"name": "doctor", "mcp_tool": None, "cli": "kairix doctor", "category": "diagnostic"}],
        correlation_id_fn=lambda: "cid",
    )
    out, err = io.StringIO(), io.StringIO()
    code = recommend_main(["is the system healthy?"], out=out, err=err, deps=deps, flag_reader=lambda: True)
    assert code == 0
    text = out.getvalue()
    assert "call: kairix doctor" in text, f"CLI-only cap must show its cli invocation; got:\n{text}"


def test_builder_vec_leg_failure_logs_with_traceback(caplog) -> None:
    """A vec-leg failure logs with ``exc_info=True`` (keeps the traceback).

    Mutation anchor (lives here, not in tests/knowledge/, so it falls inside
    mutation_parity's combined first-40 impacted-file window): flipping
    ``exc_info=True`` to ``False`` in ``_embed_capabilities_safe`` drops the
    traceback and this assertion fails. References
    ``kairix.knowledge.capabilities.builder`` so this file is in the
    builder module's impacted set.
    """
    import logging

    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
        build_capability_corpus,
    )

    class _BoomVecIndex:
        def add_vectors(self, hash_seqs, vectors) -> int:
            raise RuntimeError("vec index is read-only")

        def save(self) -> None:  # pragma: no cover - never reached (add raises first)
            raise AssertionError("save must not run when add_vectors raised")

    deps = CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(
            catalogue_fn=lambda: [
                {"name": "search", "mcp_tool": "search", "cli": "kairix search", "category": "retrieval"}
            ],
            now_fn=lambda: "2026-06-20T00:00:00+00:00",
        ),
        chunk_writer_fn=lambda _db: _CountingWriter(),
        embed_batch_fn=lambda texts: [[0.5, 0.5] for _ in texts],
        vec_index_fn=lambda: _BoomVecIndex(),
    )
    with caplog.at_level(logging.WARNING):
        result = build_capability_corpus(object(), deps=deps)

    # The BM25 write survived the vec-leg failure (the T1 deferred fix).
    assert result.written == 1
    assert result.error == ""
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "vec leg unavailable" in r.getMessage()]
    assert warnings, "expected a vec-leg-unavailable WARNING"
    assert warnings[0].exc_info is not None, "vec-leg warning must carry the traceback (exc_info=True)"
    assert warnings[0].exc_info[0] is RuntimeError


class _CountingWriter:
    """Capture-only chunk writer — reports the upserted count."""

    def upsert(self, chunks) -> int:
        return len(list(chunks))
