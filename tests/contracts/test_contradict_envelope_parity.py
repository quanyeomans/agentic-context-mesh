"""Contract: ``ContradictOutput`` <-> envelope round-trip preserves rendered text.

PR 2.6 / #421 — warm-MCP text-mode routing for ``kairix contradict``.

After this PR the CLI dispatcher can route ``kairix contradict check
<content>`` to a warm MCP worker even when ``--json`` is not on argv.
The dispatcher receives a JSON envelope (the same dict
``tool_contradict`` returns); to render the operator-facing text it
converts envelope -> ``ContradictOutput`` via
``ContradictOutput.from_envelope`` and calls the existing
``format_text``. That seam MUST produce byte-identical text to the
in-process path — otherwise warm-MCP routing silently changes operator
output.

This contract pins that round-trip at the byte level for every relevant
shape (empty / contradictions-found / error). Production callers never
construct ``ContradictOutput`` from a dict directly; the test goes
through the public surface (``contradict_output_to_envelope`` +
``ContradictOutput.from_envelope``) so the contract documents the
supported shape and breaks loudly when either side drifts.
"""

from __future__ import annotations

import pytest

from kairix.knowledge.contradict.cli import format_text
from kairix.use_cases.contradict import (
    ContradictionHit,
    ContradictOutput,
    contradict_output_to_envelope,
)

pytestmark = pytest.mark.contract


def _roundtrip(out: ContradictOutput) -> ContradictOutput:
    """Project ``out`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = contradict_output_to_envelope(out)
    return ContradictOutput.from_envelope(envelope)


# Sabotage-proof (executed): dropped the ``content`` key from
# ``from_envelope`` (set to ""); the empty-contradictions branch in
# format_text still rendered the same string because format_text never
# reads content — but the structural assertion on rebuilt.content
# fired. Restored.
def test_roundtrip_preserves_text_with_no_contradictions() -> None:
    original = ContradictOutput(
        content="agent-alpha proposes the sky is green.",
        contradictions=[],
        has_contradictions=False,
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original, top_k=5, threshold=0.45)
    rendered_rebuilt = format_text(rebuilt, top_k=5, threshold=0.45)
    assert rendered_original == rendered_rebuilt
    assert "No contradictions found" in rendered_rebuilt
    # Structural anchor — confirm round-trip preserves content too.
    assert rebuilt.content == original.content


# Sabotage-proof (executed): mutated ``from_envelope`` to hard-code
# ``score=0.0`` for every hit; rendered text now shows "Score: 0.00"
# instead of "Score: 0.85", equality assertion fired; restored.
def test_roundtrip_preserves_text_with_contradictions_found() -> None:
    original = ContradictOutput(
        content="The sky is green.",
        contradictions=[
            ContradictionHit(
                path="docs/architecture/sky.md",
                score=0.85,
                reason="Existing document states the sky is blue.",
                snippet="The sky is blue and clear during daylight hours.",
                category="direct",
                claim="sky color",
            ),
            ContradictionHit(
                path="docs/notes/colour.md",
                score=0.62,
                reason="Note describes sky as azure.",
                snippet="On a clear day the sky appears azure.",
                category="indirect",
                claim="sky color",
            ),
        ],
        has_contradictions=True,
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original, top_k=5, threshold=0.45)
    rendered_rebuilt = format_text(rebuilt, top_k=5, threshold=0.45)
    assert rendered_original == rendered_rebuilt
    # Anchor on the score string the formatter prints — drift in
    # ``from_envelope.score`` reads would change this.
    assert "Score: 0.85" in rendered_rebuilt
    assert "docs/architecture/sky.md" in rendered_rebuilt


# Sabotage-proof (executed): made ``from_envelope`` drop the ``error``
# key; format_text now took the "No contradictions found" branch
# instead of the "error:" branch, equality fired; restored.
def test_roundtrip_preserves_text_with_error_envelope() -> None:
    original = ContradictOutput(
        content="any claim",
        contradictions=[],
        has_contradictions=False,
        error="RuntimeError: search unavailable",
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original, top_k=5, threshold=0.45)
    rendered_rebuilt = format_text(rebuilt, top_k=5, threshold=0.45)
    assert rendered_original == rendered_rebuilt
    assert rendered_rebuilt.startswith("error:")
    assert "RuntimeError: search unavailable" in rendered_rebuilt


# Sabotage-proof (executed): removed the ``has_contradictions`` field
# extraction from ``from_envelope`` (defaulted to False); the explicit
# assertion on ``rebuilt.has_contradictions`` fired when the original
# value was True. Restored.
def test_roundtrip_preserves_structural_fields() -> None:
    original = ContradictOutput(
        content="claim body",
        contradictions=[
            ContradictionHit(
                path="p.md",
                score=0.5,
                reason="r",
                snippet="s",
                category="direct",
                claim="c",
            ),
        ],
        has_contradictions=True,
        error="",
    )
    rebuilt = _roundtrip(original)
    assert rebuilt.content == original.content
    assert rebuilt.has_contradictions == original.has_contradictions
    assert rebuilt.error == original.error
    assert len(rebuilt.contradictions) == len(original.contradictions)
    for orig_hit, rebuilt_hit in zip(original.contradictions, rebuilt.contradictions, strict=True):
        assert rebuilt_hit.path == orig_hit.path
        assert rebuilt_hit.score == orig_hit.score
        assert rebuilt_hit.reason == orig_hit.reason
        assert rebuilt_hit.snippet == orig_hit.snippet
        assert rebuilt_hit.category == orig_hit.category
        assert rebuilt_hit.claim == orig_hit.claim


# Sabotage-proof (executed): mutated ``from_envelope`` to read
# ``envelope.get("contradictions", [{}])`` so a missing key yielded one
# empty hit rather than zero hits; the assertion on
# ``len(rebuilt.contradictions) == 0`` fired. Restored.
def test_roundtrip_preserves_missing_contradictions_as_empty_list() -> None:
    """An envelope dict missing the ``contradictions`` key must round-trip to []."""
    envelope_minus_contradictions = {
        "content": "claim",
        "has_contradictions": False,
        "error": "",
    }
    rebuilt = ContradictOutput.from_envelope(envelope_minus_contradictions)
    assert rebuilt.contradictions == []
    assert rebuilt.content == "claim"
    assert rebuilt.has_contradictions is False
    assert rebuilt.error == ""
