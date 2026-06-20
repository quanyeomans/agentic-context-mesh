"""F30 outcome test — ``tool_recommend`` MCP direct-handler surface.

Per the F30 contract: call ``tool_recommend`` directly with deps injected
and assert on the returned envelope via Subscript / Attribute access — not
internal call-counts. The recommender's MCP adapter is a thin wrapper
around ``run_recommend`` + ``recommend_output_to_envelope``, gated by the
``recommender`` flag at the adapter level.

DI seam: ``tool_recommend`` forwards ``deps`` to ``run_recommend`` and
reads the flag via the injected ``flag_reader`` — production callers leave
both at their defaults. Tests pass a ``RecommendDeps`` backed by
``FakeSearchPipeline`` and a flag_reader returning True so the call
exercises the composed adapter → use case → envelope path without a
provider, index, or env var (F1/F2-clean).

Sabotage-proof anchor: dropping the ``recommendations`` key build in
``recommend_output_to_envelope`` (e.g. returning ``{}``) fails the
recommendations assertion below; routing the OFF branch to ``run_recommend``
fails ``test_tool_recommend_flag_off_returns_disabled_envelope``. Verified
locally (Step 4.5 report).
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.server import tool_recommend_capabilities
from tests.fakes import FakeSearchPipeline

pytestmark = pytest.mark.integration

# ``tool_recommend_capabilities`` is the ``tool_<registered-tool-name>``
# alias of ``tool_recommend`` (registered MCP tool: recommend_capabilities).
# The F30 outcome-test scan keys on this name; calling it here exercises the
# exact handler the MCP surface registers.


def _flag_on() -> bool:
    return True


def _deps_with_contradict_hit() -> object:
    from kairix.use_cases.recommend import RecommendDeps

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict#0",
                title="contradict",
                content="Check new content against existing knowledge.",
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


def test_tool_recommend_envelope_carries_ranked_recommendation() -> None:
    """The envelope carries a ranked recommendation with a ready-to-call binding."""
    envelope = tool_recommend_capabilities(
        task="are there conflicting facts?",
        deps=_deps_with_contradict_hit(),
        flag_reader=_flag_on,
    )

    assert envelope["error"] == ""
    assert envelope["task"] == "are there conflicting facts?"
    assert envelope["correlation_id"] == "fixed-id"
    rec = envelope["recommendations"][0]
    assert rec["name"] == "contradict"
    assert rec["mcp_tool"] == "contradict"
    assert rec["cli"] == "kairix contradict"
    assert rec["kind"] == "tool"


def test_tool_recommend_flag_off_returns_disabled_envelope() -> None:
    """Flag OFF — disabled envelope, no recs, ``run_recommend`` not consulted."""
    envelope = tool_recommend_capabilities(
        task="anything",
        deps=_deps_with_contradict_hit(),
        flag_reader=lambda: False,
    )

    assert envelope["recommendations"] == []
    assert "recommender is disabled" in envelope["error"]
