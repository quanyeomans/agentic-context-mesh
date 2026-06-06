"""Contract: ``BootstrapOutput`` <-> envelope round-trip preserves rendered markdown.

PR 2.3 / #421 — warm-MCP text-mode routing for ``kairix bootstrap``.

After this PR the CLI dispatcher can route ``kairix bootstrap <agent>``
to a warm MCP worker even when ``--json`` is not on argv. The
dispatcher receives a JSON envelope (the same dict ``tool_bootstrap``
returns); to render the operator-facing markdown it converts envelope
-> ``BootstrapOutput`` via ``BootstrapOutput.from_envelope`` and calls
the existing ``bootstrap_output_to_markdown``. That seam MUST produce
byte-identical text to the in-process path — otherwise warm-MCP
routing silently changes operator output.

This contract pins that round-trip at the byte level for every
relevant shape: empty result, populated with role/board/memory/goals,
with degraded health, with error envelope. Production callers never
construct ``BootstrapOutput`` from a dict directly; the test goes
through the public surface (``bootstrap_output_to_envelope`` +
``BootstrapOutput.from_envelope``) so the contract documents the
supported shape and breaks loudly when either side drifts.
"""

from __future__ import annotations

import pytest

from kairix.use_cases.bootstrap import (
    BootstrapHealth,
    BootstrapOutput,
    MemoryEntry,
    bootstrap_output_to_envelope,
    bootstrap_output_to_markdown,
)

pytestmark = pytest.mark.contract


def _roundtrip(out: BootstrapOutput) -> BootstrapOutput:
    """Project ``out`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = bootstrap_output_to_envelope(out)
    return BootstrapOutput.from_envelope(envelope)


# Sabotage-proof (executed): mutated ``from_envelope`` to read
# ``envelope.get("agent", "wrong")``; the markdown header line
# "# Bootstrap envelope: wrong" diverged from the original, the byte
# equality assertion fired with the header diff; restored.
def test_roundtrip_preserves_markdown_for_empty_result() -> None:
    """A bare BootstrapOutput (no role / board / memory / goals) renders the
    same markdown after the envelope round-trip — the empty-section
    placeholders (``_(no Board.md found)_``, etc.) survive."""
    original = BootstrapOutput(agent="agent-alpha")
    rebuilt = _roundtrip(original)
    assert bootstrap_output_to_markdown(original) == bootstrap_output_to_markdown(rebuilt)


# Sabotage-proof (executed): set ``from_envelope`` to ignore the
# ``recent_memory`` key (always []); the rendered markdown lost the
# "### 2026-05-14" section heading and the byte-equality assertion
# fired on the memory section diff; restored.
def test_roundtrip_preserves_markdown_for_populated_result() -> None:
    """Full envelope — role + board + memory + goals — round-trips
    byte-identical. This is the canonical happy-path shape the warm-MCP
    dispatcher will route."""
    original = BootstrapOutput(
        agent="agent-alpha",
        role="Builder — agent-alpha",
        board="priorities: ship the envelope composer",
        recent_memory=[
            MemoryEntry(date="2026-05-14", content="today: progress"),
            MemoryEntry(date="2026-05-13", content="yesterday: planning"),
        ],
        active_goals=["land PR 2.3", "wire warm-mcp"],
        next_action="Read your Board for current priorities, then call tool_search.",
    )
    rebuilt = _roundtrip(original)
    rendered_original = bootstrap_output_to_markdown(original)
    rendered_rebuilt = bootstrap_output_to_markdown(rebuilt)
    assert rendered_original == rendered_rebuilt
    # Anchor: the rebuilt markdown must carry the memory date headings
    # and goal bullets — both fed from the envelope round-trip.
    assert "### 2026-05-14" in rendered_rebuilt
    assert "- land PR 2.3" in rendered_rebuilt


# Sabotage-proof (executed): hard-wired the health round-trip to drop
# ``vector_search`` (always "ok"); the degraded-mode markdown line
# "- vector_search: degraded" was replaced with "ok", byte equality
# fired; restored.
def test_roundtrip_preserves_markdown_with_degraded_health_subsection() -> None:
    """The ``## Health`` markdown section is reconstructed from the
    envelope's ``health`` dict — every field (vector_search / bm25 /
    chat / secrets_loaded / degraded_reason) must round-trip."""
    original = BootstrapOutput(
        agent="agent-beta",
        role="Shape",
        board="board body",
        health=BootstrapHealth(
            vector_search="degraded",
            bm25="ok",
            chat="offline",
            secrets_loaded=False,
            degraded_reason="LLM credentials missing",
            next_action="Run 'kairix onboard check' to wire creds.",
        ),
        next_action="Run 'kairix onboard check' to wire creds.",
    )
    rebuilt = _roundtrip(original)
    rendered_original = bootstrap_output_to_markdown(original)
    rendered_rebuilt = bootstrap_output_to_markdown(rebuilt)
    assert rendered_original == rendered_rebuilt
    # Anchor: the degraded_reason line only renders when health.degraded_reason
    # is non-empty — proves the health field round-trips faithfully.
    assert "- degraded_reason: LLM credentials missing" in rendered_rebuilt
    assert "- secrets_loaded: False" in rendered_rebuilt


# Sabotage-proof (executed): dropped the ``error`` key extraction
# from ``from_envelope`` (left as ""); the header **Error:** preamble
# disappeared from the rebuilt markdown, byte equality fired; restored.
def test_roundtrip_preserves_markdown_for_error_envelope() -> None:
    """When the use case populated ``error`` (e.g. document root
    missing), the markdown header includes an ``**Error:**`` preamble.
    The error string must round-trip so the rebuilt envelope renders the
    same diagnostic."""
    original = BootstrapOutput(
        agent="agent-gamma",
        health=BootstrapHealth(),
        next_action=("Configure KAIRIX_DOCUMENT_ROOT or ask your admin — the document root does not exist."),
        error="DocumentRootMissing: /nope/does-not-exist",
    )
    rebuilt = _roundtrip(original)
    rendered_original = bootstrap_output_to_markdown(original)
    rendered_rebuilt = bootstrap_output_to_markdown(rebuilt)
    assert rendered_original == rendered_rebuilt
    assert "**Error:** DocumentRootMissing" in rendered_rebuilt
    # And the error field survived for shell pipelines / CLI exit logic.
    assert rebuilt.error == original.error


# Sabotage-proof (executed): made ``from_envelope`` skip the
# ``active_goals`` key (always []); the bullet list collapsed to the
# ``_(no Goals.md found)_`` placeholder, byte equality fired; restored.
def test_roundtrip_preserves_structural_fields() -> None:
    """Every field on BootstrapOutput survives the round-trip — guards
    against silent drift where one field is dropped from either
    ``to_envelope`` or ``from_envelope``."""
    original = BootstrapOutput(
        agent="agent-alpha",
        role="Builder",
        board="b",
        recent_memory=[MemoryEntry(date="2026-05-14", content="m")],
        active_goals=["g1", "g2"],
        health=BootstrapHealth(vector_search="ok", bm25="ok", chat="ok"),
        next_action="na",
        error="",
    )
    rebuilt = _roundtrip(original)
    assert rebuilt.agent == original.agent
    assert rebuilt.role == original.role
    assert rebuilt.board == original.board
    assert rebuilt.recent_memory == original.recent_memory
    assert rebuilt.active_goals == list(original.active_goals)
    assert rebuilt.health.vector_search == original.health.vector_search
    assert rebuilt.health.bm25 == original.health.bm25
    assert rebuilt.health.chat == original.health.chat
    assert rebuilt.health.secrets_loaded == original.health.secrets_loaded
    assert rebuilt.next_action == original.next_action
    assert rebuilt.error == original.error
