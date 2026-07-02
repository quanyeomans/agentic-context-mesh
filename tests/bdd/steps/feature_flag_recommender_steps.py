"""Step definitions for feature_flag_recommender.feature (F54 both-branch).

Drives both flag-gated recommender surfaces through their production
composition surfaces with the flag value pinned through the canonical
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``:

  * the adapter gate via ``kairix.agents.mcp.server.tool_recommend_capabilities``
    (``flag_reader`` seam), and
  * the worker boot hook via
    ``kairix.worker.maybe_build_capability_corpus_at_boot`` (``read_flag``
    seam).

No ``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars (F1/F2-clean).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.server import tool_recommend_capabilities
from kairix.use_cases.recommend import RecommendDeps
from kairix.worker import maybe_build_capability_corpus_at_boot
from tests.fakes import FakeFeatureFlagResolver, FakeSearchPipeline

pytestmark = pytest.mark.bdd


@dataclass
class _FlagState:
    resolver: FakeFeatureFlagResolver | None = None
    has_contradict: bool = False
    envelope: dict[str, Any] = field(default_factory=dict)
    corpus_result: Any = None
    db_opened: int = 0
    tmp_path: Path | None = None


@pytest.fixture
def _flag_state(tmp_path: Path) -> _FlagState:
    return _FlagState(tmp_path=tmp_path)


@given(parsers.parse("the operator has the recommender flag set to {value}"))
def _set_flag(_flag_state: _FlagState, value: str) -> None:
    parsed = value.strip().lower() == "true"
    _flag_state.resolver = FakeFeatureFlagResolver().with_flag("recommender", parsed)


@given("the flagged toolset includes a way to check content for conflicts")
def _has_contradict(_flag_state: _FlagState) -> None:
    _flag_state.has_contradict = True


def _adapter_deps(state: _FlagState) -> RecommendDeps:
    rows = []
    catalogue: list[dict[str, Any]] = []
    if state.has_contradict:
        rows.append(
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict",
                title="contradict",
                content="Check new content for conflicts.",
            )
        )
        catalogue.append(
            {
                "name": "contradict",
                "mcp_tool": "contradict",
                "cli": "kairix contradict",
                "category": "synthesis",
                "when_to_use": "Check for conflicts.",
            }
        )
    fake = FakeSearchPipeline(scripted_results=rows)
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: catalogue,
        correlation_id_fn=lambda: "cid",
    )


@when("the agent asks the recommend surface which tool fits a task")
def _ask_surface(_flag_state: _FlagState) -> None:
    assert _flag_state.resolver is not None
    _flag_state.envelope = tool_recommend_capabilities(
        task="check this against what we know",
        deps=_adapter_deps(_flag_state),
        flag_reader=lambda: _flag_state.resolver.get("recommender"),
    )


def _corpus_deps() -> Any:
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
    )

    return CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(
            catalogue_fn=lambda: [
                {"name": "contradict", "mcp_tool": "contradict", "cli": "kairix contradict", "category": "synthesis"},
            ],
            now_fn=lambda: "2026-06-20T00:00:00+00:00",
        ),
        embed_batch_fn=lambda texts: [],  # BM25-only
    )


@when("the worker boot corpus-build hook runs")
def _worker_hook_runs(_flag_state: _FlagState) -> None:
    assert _flag_state.resolver is not None
    assert _flag_state.tmp_path is not None
    db_path = _flag_state.tmp_path / "index.sqlite"

    def _db_factory() -> sqlite3.Connection:
        _flag_state.db_opened += 1
        from kairix.core.db.schema import create_schema

        db = sqlite3.connect(db_path)
        create_schema(db)
        return db

    _flag_state.corpus_result = maybe_build_capability_corpus_at_boot(
        read_flag=_flag_state.resolver.get,
        db_factory=_db_factory,
        corpus_deps=_corpus_deps(),
    )


@then("the recommend surface reports the recommender is disabled")
def _surface_disabled(_flag_state: _FlagState) -> None:
    assert "recommender is disabled" in _flag_state.envelope["error"]


@then("the recommend surface returns no recommendations")
def _surface_no_recs(_flag_state: _FlagState) -> None:
    assert _flag_state.envelope["recommendations"] == []


@then("the recommend surface ranks the conflict-checking tool")
def _surface_ranks_contradict(_flag_state: _FlagState) -> None:
    names = [r["name"] for r in _flag_state.envelope["recommendations"]]
    assert "contradict" in names, f"expected contradict ranked; got {names!r}"


@then("the recommend surface reports no error")
def _surface_no_error(_flag_state: _FlagState) -> None:
    assert _flag_state.envelope["error"] == ""


@then("the worker does not build the capability corpus")
def _worker_noop(_flag_state: _FlagState) -> None:
    assert _flag_state.corpus_result is None, "OFF branch must not build the corpus"
    assert _flag_state.db_opened == 0, "OFF branch must not open the DB"


@then("the worker builds the capability corpus")
def _worker_builds(_flag_state: _FlagState) -> None:
    assert _flag_state.corpus_result is not None, "ON branch must build the corpus"
    assert _flag_state.corpus_result.written == 1
    assert _flag_state.corpus_result.error == ""
