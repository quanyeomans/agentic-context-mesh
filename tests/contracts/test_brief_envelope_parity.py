"""Contract: ``BriefOutput`` <-> envelope round-trip preserves rendered text.

PR 2.1 / #421 — warm-MCP text-mode routing for ``kairix brief``.

After this PR the CLI dispatcher can route ``kairix brief <agent>`` to a
warm MCP worker even when ``--json`` is not on argv. The dispatcher
receives a JSON envelope (the same dict ``tool_brief`` returns); to
render the operator-facing text it converts envelope -> ``BriefOutput``
via ``BriefOutput.from_envelope`` and calls the existing
``format_output``. That seam MUST produce byte-identical text to the
in-process path — otherwise warm-MCP routing silently changes operator
output.

This contract pins that round-trip at the byte level for every relevant
shape (with content, with error, with empty content, with full-print on
and off). Production callers never construct ``BriefOutput`` from a dict
directly; the test goes through the public surface (``brief_output_to_envelope``
+ ``BriefOutput.from_envelope``) so the contract documents the supported
shape and breaks loudly when either side drifts.
"""

from __future__ import annotations

import pytest

from kairix.agents.briefing.cli import format_output
from kairix.use_cases.brief import BriefOutput, brief_output_to_envelope

pytestmark = pytest.mark.contract


def _roundtrip(out: BriefOutput) -> BriefOutput:
    """Project ``out`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = brief_output_to_envelope(out)
    return BriefOutput.from_envelope(envelope)


# Sabotage-proof (executed): dropped the ``content`` key from
# ``from_envelope`` (set to ""), test failed on the content-equality
# assertion; restored.
def test_roundtrip_preserves_text_with_content_preview_mode() -> None:
    original = BriefOutput(
        agent="agent-alpha",
        content="\n".join(f"line {i}" for i in range(10)),
        path="/tmp/briefings/agent-alpha-latest.md",
        preview="\n".join(f"line {i}" for i in range(10)),
    )
    rebuilt = _roundtrip(original)
    assert format_output(original, print_full=False) == format_output(rebuilt, print_full=False)


# Sabotage-proof (executed): mutated ``from_envelope`` to read
# ``envelope.get("path", "/wrong")``; the long-content branch in
# format_output embeds the path in the truncation footer, the equality
# assertion fired; restored.
def test_roundtrip_preserves_text_with_long_content_truncation() -> None:
    original = BriefOutput(
        agent="agent-beta",
        content="\n".join(f"row {i}" for i in range(50)),
        path="/tmp/briefings/agent-beta-latest.md",
        preview="\n".join(f"row {i}" for i in range(30)),
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_output(original, print_full=False)
    rendered_rebuilt = format_output(rebuilt, print_full=False)
    assert rendered_original == rendered_rebuilt
    # Anchor the truncation footer — it must carry the path and the line
    # remainder, both fed from the envelope round-trip.
    assert "20 more lines" in rendered_rebuilt
    assert "/tmp/briefings/agent-beta-latest.md" in rendered_rebuilt


# Sabotage-proof (executed): made ``from_envelope`` return an instance
# with content="" regardless of input; print_full path emitted empty
# string, equality fired; restored.
def test_roundtrip_preserves_text_with_full_print_mode() -> None:
    original = BriefOutput(
        agent="agent-gamma",
        content="\n".join(f"r{i}" for i in range(40)),
        path="/tmp/briefings/agent-gamma-latest.md",
        preview="\n".join(f"r{i}" for i in range(30)),
    )
    rebuilt = _roundtrip(original)
    assert format_output(original, print_full=True) == format_output(rebuilt, print_full=True)


# Sabotage-proof (executed): mutated ``from_envelope`` to default
# content to "non-empty" when the envelope value was ""; the empty-content
# branch in format_output now returned text where original returned "",
# equality fired; restored.
def test_roundtrip_preserves_text_with_empty_content_error_envelope() -> None:
    original = BriefOutput(
        agent="ghost",
        content="",
        path="",
        preview="",
        error="InvalidAgent: 'ghost' resolves to no configured surface. fix: run `kairix onboard agent --name <name>`.",
    )
    rebuilt = _roundtrip(original)
    # Empty content -> format_output returns "" on both sides.
    assert format_output(original, print_full=False) == ""
    assert format_output(rebuilt, print_full=False) == ""
    # The error string survives the round-trip so the CLI's stderr
    # branch can read it.
    assert rebuilt.error == original.error


# Sabotage-proof (executed): removed the ``agent`` key extraction from
# ``from_envelope`` (hard-coded ""); assertion on ``rebuilt.agent``
# fired; restored.
def test_roundtrip_preserves_structural_fields() -> None:
    original = BriefOutput(
        agent="agent-alpha",
        content="body",
        path="/tmp/p.md",
        preview="body",
        error="",
    )
    rebuilt = _roundtrip(original)
    assert rebuilt.agent == original.agent
    assert rebuilt.content == original.content
    assert rebuilt.path == original.path
    assert rebuilt.preview == original.preview
    assert rebuilt.error == original.error
