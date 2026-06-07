"""Contract: ``ResearchOutput`` <-> envelope round-trip preserves rendered text.

PR 2.5 / #421 — warm-MCP text-mode routing for ``kairix research``.

After this PR the CLI dispatcher can route ``kairix research <query>``
to a warm MCP worker even when ``--json`` is not on argv. The dispatcher
receives a JSON envelope (the same dict ``tool_research`` returns); to
render the operator-facing text it converts envelope -> ``ResearchOutput``
via ``ResearchOutput.from_envelope`` and calls the existing
``format_text``. That seam MUST produce byte-identical text to the
in-process path — otherwise warm-MCP routing silently changes operator
output.

This contract pins that round-trip at the byte level for every relevant
shape (populated synthesis, empty synthesis, error envelope, chunk
truncation footer, gaps list). Production callers never construct
``ResearchOutput`` from a dict directly; the test goes through the
public surface (``research_output_to_envelope`` +
``ResearchOutput.from_envelope``) so the contract documents the
supported shape and breaks loudly when either side drifts.
"""

from __future__ import annotations

import pytest

from kairix.agents.research.cli import format_text
from kairix.use_cases.research import ResearchOutput, research_output_to_envelope

pytestmark = pytest.mark.contract


def _roundtrip(out: ResearchOutput) -> ResearchOutput:
    """Project ``out`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = research_output_to_envelope(out)
    return ResearchOutput.from_envelope(envelope)


# Sabotage-proof (executed): hard-coded ``synthesis="SABOTAGE"`` in
# ``from_envelope``; the populated-synthesis test fired on the
# format_text byte-equality assertion because the rebuilt text carried
# "SABOTAGE" in the Synthesis block. Restored.
def test_roundtrip_preserves_text_with_populated_synthesis() -> None:
    original = ResearchOutput(
        query="how does the warm-MCP path render text?",
        synthesis="The CLI dispatcher rebuilds a ResearchOutput from the envelope dict.",
        turns=3,
        confidence=0.75,
    )
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)


# Sabotage-proof (executed): dropped the ``gaps`` key handling in
# ``from_envelope`` (set to []); the rebuilt text dropped the
# "Gaps / open questions:" block + bullet, byte-equality fired. Restored.
def test_roundtrip_preserves_text_with_gaps_and_chunks() -> None:
    original = ResearchOutput(
        query="q",
        synthesis="answer body",
        gaps=["what about edge case X?", "missing dataset Y"],
        retrieved_chunks=[{"path": "/c1"}, {"path": "/c2"}, {"path": "/c3"}],
        turns=2,
        confidence=0.55,
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original)
    rendered_rebuilt = format_text(rebuilt)
    assert rendered_original == rendered_rebuilt
    # Anchor specific text the envelope round-trip carries.
    assert "what about edge case X?" in rendered_rebuilt
    assert "/c1" in rendered_rebuilt


# Sabotage-proof (executed): mutated ``from_envelope`` to truncate
# retrieved_chunks to the first 4 entries; the rebuilt text reported
# the wrong truncation footer (the rebuilt only had 4 chunks so the
# "3 more" line disappeared), byte-equality fired on the
# chunk-truncation line. Restored.
def test_roundtrip_preserves_text_with_chunk_truncation_footer() -> None:
    original = ResearchOutput(
        query="q",
        synthesis="s",
        retrieved_chunks=[{"path": f"/c{i}"} for i in range(8)],
        turns=1,
        confidence=0.4,
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original)
    rendered_rebuilt = format_text(rebuilt)
    assert rendered_original == rendered_rebuilt
    # The truncation footer is the contract-load-bearing line; assert
    # it survived.
    assert "3 more" in rendered_rebuilt


# Sabotage-proof (executed): hard-coded ``from_envelope`` to ignore the
# envelope's ``error`` key (always ""); the rebuilt format_text returned
# the populated body instead of the "error: ..." short-circuit string;
# byte-equality fired. Restored.
def test_roundtrip_preserves_text_with_error_envelope() -> None:
    original = ResearchOutput(
        query="q",
        error="ConnectionError: LLM unreachable",
    )
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)
    # The error branch short-circuits format_text.
    assert format_text(rebuilt).startswith("error:")
    assert "ConnectionError" in format_text(rebuilt)


# Sabotage-proof (executed): mutated ``from_envelope`` to coerce empty
# synthesis to "(no synthesis returned)" — the assertion
# ``rebuilt.synthesis == ""`` fired because the rebuilt value now
# carried the placeholder string as the actual synthesis. Restored.
def test_roundtrip_preserves_text_with_empty_synthesis_placeholder() -> None:
    original = ResearchOutput(
        query="q",
        synthesis="",
        turns=0,
        confidence=0.0,
    )
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)
    # The placeholder appears via the ``or`` clause in format_text; the
    # underlying synthesis field is still empty.
    assert rebuilt.synthesis == ""
    assert "(no synthesis returned)" in format_text(rebuilt)


# Sabotage-proof (executed): removed the ``query`` key extraction from
# ``from_envelope`` (hard-coded ""); the ``rebuilt.query`` equality
# assertion fired. Restored.
def test_roundtrip_preserves_structural_fields() -> None:
    original = ResearchOutput(
        query="q",
        synthesis="s",
        retrieved_chunks=[{"path": "/c1"}],
        gaps=["g1"],
        confidence=0.42,
        turns=2,
        error="",
    )
    rebuilt = _roundtrip(original)
    assert rebuilt.query == original.query
    assert rebuilt.synthesis == original.synthesis
    assert rebuilt.retrieved_chunks == original.retrieved_chunks
    assert rebuilt.gaps == original.gaps
    assert rebuilt.confidence == original.confidence
    assert rebuilt.turns == original.turns
    assert rebuilt.error == original.error
