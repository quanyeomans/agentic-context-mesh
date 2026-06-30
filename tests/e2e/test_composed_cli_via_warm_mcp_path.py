"""E2E composed path: CLI text mode rendered via warm MCP is byte-identical to in-process.

PR 2.8 / #421 — F48 sibling to ``tests/e2e/test_composed_production_path.py``.

For every composer-equipped subcommand (brief / search / bootstrap /
prep / research / contradict / timeline), build a result object two ways:

1. In-process: directly via the use-case's existing public seam (a
   dataclass result populated from a known envelope).
2. Via warm MCP: run that same envelope through the dispatcher path —
   ``register_composer`` lookup → ``from_envelope`` → ``format_text``.

Then assert the two rendered text strings are byte-identical. This is
the regression net: any drift between the in-process formatter and the
warm-MCP composer's text output is the bug PR 2.8 ships to prevent.

Per F48 + F47 + F46: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; composes through the
canonical CLI/MCP composer pair (no direct pipeline construction).
"""

from __future__ import annotations

import pytest

# Importing this side-effect-loads every PR 2.1-2.7 composer into the
# registry. Without this import, ``get_composer("brief")`` etc. return
# None. F50: this is the canonical wiring file, NOT a baseline.
import kairix.agents.mcp._composer_init  # noqa: F401 — side-effect registration
from kairix.agents.mcp.text_mode_composers import get_composer

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixture: every composer must appear in the registry post-import
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the brief entry from
# ``_composer_init.py``; this test failed because get_composer("brief")
# returned None. Restored.
@pytest.mark.parametrize(
    "subcommand",
    ["brief", "search", "bootstrap", "prep", "research", "contradict", "timeline"],
)
def test_every_composer_subcommand_is_registered(subcommand: str) -> None:
    """All 7 PR 2.1-2.7 composers must be registered via _composer_init."""
    entry = get_composer(subcommand)
    assert entry is not None, (
        f"composer for {subcommand!r} not in registry. "
        f"fix: add it to kairix/agents/mcp/_composer_init.py. "
        f"run: python -c 'from kairix.agents.mcp._composer_init import *'"
    )
    assert entry.name == subcommand


# ---------------------------------------------------------------------------
# brief — byte-identical render
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): mutated BriefOutput.from_envelope to set
# content to "" regardless of envelope; this test failed because the
# rebuilt format_output diverged from the seeded "Briefing body".
# Restored.
def test_brief_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """brief: envelope → BriefOutput.from_envelope → format_output equals in-process text."""
    from kairix.agents.briefing.cli import format_output
    from kairix.use_cases.brief import BriefOutput, brief_output_to_envelope

    in_process = BriefOutput(
        agent="agent-alpha",
        content="Briefing body for agent-alpha.\nLine two.\nLine three.",
        path="/path/to/briefing.md",
        preview="Briefing body preview",
        error="",
    )
    envelope = brief_output_to_envelope(in_process)

    composer = get_composer("brief")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = format_output(in_process, print_full=False)
    via_warm_text = composer.format_text(via_warm, [])
    assert in_process_text == via_warm_text, (
        f"text drift:\n--- in-process ---\n{in_process_text!r}\n--- warm-MCP ---\n{via_warm_text!r}"
    )


# ---------------------------------------------------------------------------
# search — byte-identical render
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): truncated SearchHit.from_envelope to drop
# the path field; this test failed because the rebuilt format_text
# rendered "Source: " instead of the seeded path. Restored.
def test_search_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """search: envelope round-trip renders byte-identical text."""
    from kairix.core.search.cli import format_text
    from kairix.use_cases.search import SearchHit, SearchOutput, search_output_to_envelope

    in_process = SearchOutput(
        query="needle",
        intent="semantic",
        results=[
            SearchHit(
                path="/p/doc-1.md",
                title="agent-alpha brief",
                snippet="The agent-alpha brief covers planning",
                score=0.42,
            ),
            SearchHit(
                path="/p/doc-2.md",
                title="agent-beta plan",
                snippet="The agent-beta plan rolls out next",
                score=0.31,
            ),
        ],
        bm25_count=2,
        vec_count=0,
        fused_count=2,
    )
    envelope = search_output_to_envelope(in_process)

    composer = get_composer("search")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = format_text(in_process)
    via_warm_text = composer.format_text(via_warm, [])
    assert in_process_text == via_warm_text


# ---------------------------------------------------------------------------
# bootstrap — byte-identical render
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): replaced bootstrap_output_to_markdown
# with a constant string in the composer's format_text; this test
# failed because the rebuilt text drifted from the in-process text.
# Restored the production formatter.
def test_bootstrap_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """bootstrap: envelope round-trip renders byte-identical markdown."""
    from kairix.use_cases.bootstrap import (
        BootstrapOutput,
        bootstrap_output_to_envelope,
        bootstrap_output_to_markdown,
    )

    in_process = BootstrapOutput(
        agent="agent-alpha",
        role="Builder — agent-alpha",
        board="priorities: ship PR 2.8",
        active_goals=["land PR 2.8", "wire warm-mcp"],
    )
    envelope = bootstrap_output_to_envelope(in_process)
    composer = get_composer("bootstrap")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = bootstrap_output_to_markdown(in_process)
    via_warm_text = composer.format_text(via_warm, [])
    assert in_process_text == via_warm_text


# ---------------------------------------------------------------------------
# prep — byte-identical render
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made the composer return a constant
# "no-results" string; this test failed because the rebuilt text did
# not match the in-process body. Restored.
def test_prep_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """prep: envelope round-trip renders byte-identical text."""
    from kairix.agents.prep.cli import format_text
    from kairix.core.protocols import SourceRef
    from kairix.use_cases.prep import PrepOutput, prep_output_to_envelope

    in_process = PrepOutput(
        query="topic-x",
        tier="l0",
        summary="Lightweight context for topic-x.",
        tokens=12,
        sources=[SourceRef.of(path="doc-alpha"), SourceRef.of(path="doc-beta")],
    )
    envelope = prep_output_to_envelope(in_process)

    composer = get_composer("prep")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = format_text(in_process)
    via_warm_text = composer.format_text(via_warm, [])
    assert in_process_text == via_warm_text


# ---------------------------------------------------------------------------
# research — byte-identical render
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made format_text in the composer return
# the empty string; this test failed because the in-process text was
# non-empty. Restored the production formatter.
def test_research_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """research: envelope round-trip renders byte-identical text."""
    from kairix.agents.research.cli import format_text
    from kairix.use_cases.research import ResearchOutput, research_output_to_envelope

    in_process = ResearchOutput(
        query="why X",
        synthesis="Research synthesis: X explained.",
        turns=2,
        confidence=0.7,
    )
    envelope = research_output_to_envelope(in_process)

    composer = get_composer("research")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = format_text(in_process)
    via_warm_text = composer.format_text(via_warm, [])
    assert in_process_text == via_warm_text


# ---------------------------------------------------------------------------
# contradict — byte-identical render (top_k/threshold come from argv)
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): dropped the argv → top_k extraction in
# the composer so format_text was always called with top_k=5; this
# test failed because the rebuilt rendering used 5 instead of the
# seeded 7. Restored the argv extraction.
def test_contradict_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """contradict: envelope round-trip renders byte-identical text using argv-derived top_k/threshold."""
    from kairix.knowledge.contradict.cli import format_text
    from kairix.use_cases.contradict import ContradictOutput, contradict_output_to_envelope

    in_process = ContradictOutput(
        content="agent-alpha proposes the sky is green.",
        contradictions=[],
        has_contradictions=False,
    )
    envelope = contradict_output_to_envelope(in_process)

    composer = get_composer("contradict")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = format_text(in_process, top_k=7, threshold=0.55)
    via_warm_text = composer.format_text(
        via_warm,
        ["claim X", "--top-k", "7", "--threshold", "0.55"],
    )
    assert in_process_text == via_warm_text


# ---------------------------------------------------------------------------
# timeline — byte-identical render (limit comes from argv)
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): replaced format_header in the composer
# with a no-op; this test failed because the in-process header was
# non-empty. Restored.
def test_timeline_text_mode_byte_identical_in_process_and_via_warm_mcp() -> None:
    """timeline: envelope round-trip renders byte-identical text using argv-derived limit."""
    from kairix.core.temporal.cli import format_header, format_results
    from kairix.use_cases.timeline import TimelineResult, timeline_output_to_envelope

    in_process = TimelineResult(
        original_query="when did agent-alpha join",
        rewritten_query="when did agent-alpha join",
        is_temporal=False,
        fell_back=False,
        time_window={},
        results=[],
    )
    envelope = timeline_output_to_envelope(in_process)

    composer = get_composer("timeline")
    assert composer is not None
    via_warm = composer.from_envelope(envelope)

    in_process_text = format_header(in_process, 10) + "\n\n" + format_results(in_process)
    via_warm_text = composer.format_text(via_warm, ["q", "--limit", "10"])
    assert in_process_text == via_warm_text
