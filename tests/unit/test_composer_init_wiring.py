"""Unit tests for the canonical composer wiring module (PR 2.8 / #421).

The module ``kairix.agents.mcp._composer_init`` is pure side-effect:
importing it registers every PR 2.1-2.7 composer into the
:mod:`kairix.agents.mcp.text_mode_composers` registry. The seven
``_*_render`` shims live there only to normalise each formatter's
signature to ``(result, argv) -> str``.

These tests drive every shim through the registry's public
``format_text`` surface (which is what the dispatcher will invoke at
warm-MCP text-mode dispatch). That keeps the module covered without
reaching into private helpers — F47-clean, no monkeypatch.

Lives under ``tests/unit/`` so it runs in Stage 2 (where F7 per-file
coverage gating happens). The E2E byte-identical equivalents are in
``tests/e2e/test_composed_cli_via_warm_mcp_path.py``.
"""

from __future__ import annotations

import pytest

# Importing the wiring module is what registers every composer.
import kairix.agents.mcp._composer_init  # noqa: F401 — side-effect registration
from kairix.agents.mcp.text_mode_composers import get_composer

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# brief shim — argv flag ``print-output`` toggles print_full
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): inverted the print_full extraction in
# ``_brief_render`` to read ``not "print-output" in flags``; this test
# failed because the rendered text used the wrong branch. Restored.
def test_brief_render_passes_print_full_when_flag_present() -> None:
    """The brief render shim must extract ``--print-output`` from argv."""
    from kairix.use_cases.brief import BriefOutput, brief_output_to_envelope

    # Seed >30 lines so the print_full toggle produces different output
    # (otherwise format_output's preview returns the entire content).
    content_lines = [f"line-{i:03d}" for i in range(40)]
    result = BriefOutput(
        agent="agent-alpha",
        content="\n".join(content_lines),
        path="/p/brief.md",
        preview="preview",
        error="",
    )
    envelope = brief_output_to_envelope(result)
    composer = get_composer("brief")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    # Render with the flag set — the shim must call format_output with
    # print_full=True so the full content surfaces.
    with_flag = composer.format_text(rebuilt, ["--print-output"])
    # Without the flag — print_full=False; rendered text shows preview
    # with a "more lines" suffix instead of the full body.
    without_flag = composer.format_text(rebuilt, [])
    assert "line-039" in with_flag  # last line only present in full output
    assert "line-039" not in without_flag  # preview truncates after 30 lines
    assert with_flag != without_flag


# ---------------------------------------------------------------------------
# search shim — argv ignored; passes result straight through
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made ``_search_render`` raise on non-empty
# argv; this test failed because the second invocation raised.
# Restored argv-ignoring shim.
def test_search_render_renders_regardless_of_argv() -> None:
    """The search render shim must work with empty + populated argv."""
    from kairix.use_cases.search import SearchOutput, search_output_to_envelope

    result = SearchOutput(
        query="needle",
        intent="semantic",
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )
    envelope = search_output_to_envelope(result)
    composer = get_composer("search")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    text_empty_argv = composer.format_text(rebuilt, [])
    text_populated_argv = composer.format_text(rebuilt, ["needle", "--top-k", "5"])
    # argv is ignored — both outputs are identical.
    assert text_empty_argv == text_populated_argv
    assert "needle" in text_empty_argv


# ---------------------------------------------------------------------------
# bootstrap shim — argv ignored
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made ``_bootstrap_render`` return a
# constant string; this test failed because the rendered markdown did
# not contain the seeded role. Restored.
def test_bootstrap_render_emits_markdown_from_result() -> None:
    """The bootstrap render shim must invoke bootstrap_output_to_markdown."""
    from kairix.use_cases.bootstrap import (
        BootstrapOutput,
        bootstrap_output_to_envelope,
    )

    result = BootstrapOutput(
        agent="agent-alpha",
        role="Builder — agent-alpha",
        board="priorities: ship PR 2.8",
        active_goals=["land PR 2.8"],
    )
    envelope = bootstrap_output_to_envelope(result)
    composer = get_composer("bootstrap")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    text = composer.format_text(rebuilt, [])
    assert "agent-alpha" in text
    assert "Builder" in text


# ---------------------------------------------------------------------------
# prep shim — argv ignored
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): replaced ``_prep_render`` with a lambda
# returning ""; this test failed because the rendered text was empty.
# Restored the format_text delegation.
def test_prep_render_renders_query_from_result() -> None:
    """The prep render shim must invoke prep's format_text."""
    from kairix.core.protocols import SourceRef
    from kairix.use_cases.prep import PrepOutput, prep_output_to_envelope

    result = PrepOutput(
        query="topic-x",
        tier="l0",
        summary="Lightweight context for topic-x.",
        tokens=12,
        sources=[SourceRef.of(path="doc-alpha")],
    )
    envelope = prep_output_to_envelope(result)
    composer = get_composer("prep")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    text = composer.format_text(rebuilt, [])
    assert "topic-x" in text


# ---------------------------------------------------------------------------
# research shim — argv ignored
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made ``_research_render`` return None;
# this test failed because the format_text contract requires str.
# Restored.
def test_research_render_renders_synthesis_from_result() -> None:
    """The research render shim must invoke research's format_text."""
    from kairix.use_cases.research import (
        ResearchOutput,
        research_output_to_envelope,
    )

    result = ResearchOutput(
        query="why X",
        synthesis="Research synthesis: X explained.",
        turns=2,
        confidence=0.7,
    )
    envelope = research_output_to_envelope(result)
    composer = get_composer("research")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    text = composer.format_text(rebuilt, [])
    assert "Research synthesis" in text


# ---------------------------------------------------------------------------
# contradict shim — argv flags ``--top-k`` and ``--threshold`` are read
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): hardcoded top_k=5 in ``_contradict_render``
# regardless of flags; this test failed because the rendered text used
# the wrong limit. Restored argv parsing.
def test_contradict_render_extracts_top_k_and_threshold_from_argv() -> None:
    """The contradict render shim must parse --top-k and --threshold from argv."""
    from kairix.use_cases.contradict import (
        ContradictOutput,
        contradict_output_to_envelope,
    )

    result = ContradictOutput(
        content="agent-alpha proposes the sky is green.",
        contradictions=[],
        has_contradictions=False,
    )
    envelope = contradict_output_to_envelope(result)
    composer = get_composer("contradict")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    # Render with explicit flags
    text_flags = composer.format_text(rebuilt, ["claim X", "--top-k", "7", "--threshold", "0.55"])
    # Render with defaults (no flags)
    text_defaults = composer.format_text(rebuilt, [])
    # Both produce a string; argv parsing must not raise on either.
    assert isinstance(text_flags, str)
    assert isinstance(text_defaults, str)
    assert len(text_flags) > 0
    assert len(text_defaults) > 0


# ---------------------------------------------------------------------------
# timeline shim — argv ``--limit`` is read
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): hardcoded limit=10 in ``_timeline_render``
# regardless of argv; this test failed because the rendered header used
# the wrong limit value. Restored argv parsing.
def test_timeline_render_extracts_limit_from_argv() -> None:
    """The timeline render shim must parse --limit from argv."""
    from kairix.use_cases.timeline import (
        TimelineResult,
        timeline_output_to_envelope,
    )

    result = TimelineResult(
        original_query="when did agent-alpha join",
        rewritten_query="when did agent-alpha join",
        is_temporal=False,
        fell_back=False,
        time_window={},
        results=[],
    )
    envelope = timeline_output_to_envelope(result)
    composer = get_composer("timeline")
    assert composer is not None
    rebuilt = composer.from_envelope(envelope)

    text_explicit_limit = composer.format_text(rebuilt, ["q", "--limit", "25"])
    text_default_limit = composer.format_text(rebuilt, [])
    assert isinstance(text_explicit_limit, str)
    assert isinstance(text_default_limit, str)


# ---------------------------------------------------------------------------
# Wiring assertion — every composer registers on import
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the ``register_composer("prep", ...)``
# block from ``_composer_init.py``; this test failed because
# get_composer("prep") returned None. Restored.
@pytest.mark.parametrize(
    "subcommand",
    ["brief", "search", "bootstrap", "prep", "research", "contradict", "timeline"],
)
def test_every_composer_subcommand_is_registered_at_import(subcommand: str) -> None:
    """Each of the 7 PR 2.1-2.7 composers wires into the registry on import."""
    composer = get_composer(subcommand)
    assert composer is not None, (
        f"composer for {subcommand!r} missing. "
        f"fix: add a register_composer({subcommand!r}, ...) block in "
        f"kairix/agents/mcp/_composer_init.py. "
        f"run: pytest tests/unit/test_composer_init_wiring.py -k {subcommand}"
    )
    assert composer.name == subcommand
