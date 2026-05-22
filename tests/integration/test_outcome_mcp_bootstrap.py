"""F30 outcome test — MCP ``bootstrap`` tool.

``tool_bootstrap`` (kairix/agents/mcp/server.py:514) is the thin
adapter around ``kairix.use_cases.bootstrap.run_bootstrap``. It returns
the agent orientation envelope: role / board / recent_memory /
active_goals / health / next_action.

The F30 contract for MCP tools (``scripts/checks/check_f30_operator_outcome_tests.py``):
call ``tool_<name>`` directly and assert on returned-envelope content
via Subscript/Attribute access — NOT on internal call-counts.

The DI seam is the existing ``deps`` kwarg on ``tool_bootstrap``,
already documented at server.py:524-535. Tests construct a
``BootstrapDeps(document_root_fn=lambda: tmp_path)`` so the envelope
reads from a seeded tmpdir rather than ``kairix.paths.document_root()``.

The companion CLI outcome test
(``tests/integration/test_outcome_bootstrap_cli.py``, commit
``2334a49d``) drives the same use case through the subprocess
surface. This file pays down the MCP-tool half by exercising the
direct in-process handler the agent harness invokes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.agents.mcp.server import tool_bootstrap
from kairix.use_cases.bootstrap import BootstrapDeps

pytestmark = pytest.mark.integration


def _seed_minimal_vault(root: Path, agent: str) -> None:
    """Mirror ``tests/integration/test_outcome_bootstrap_cli.py:_seed_minimal_vault``.

    Minimum content for a successful envelope: a Board, a Goals file,
    one dated memory file. All live under
    ``<root>/04-Agent-Knowledge/<agent>/``.
    """
    agent_dir = root / "04-Agent-Knowledge" / agent
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)
    (agent_dir / "Board.md").write_text("priorities: ship outcome tests", encoding="utf-8")
    (agent_dir / "Goals.md").write_text("- pay down F30 baseline\n- lift codebase standard\n", encoding="utf-8")
    (agent_dir / "memory" / "2026-05-14.md").write_text("today: outcome test green\n", encoding="utf-8")


def test_tool_bootstrap_envelope_carries_seeded_board_and_goals(tmp_path: Path) -> None:
    """``tool_bootstrap`` reads board/goals/memory from the seeded vault and
    projects them into the envelope.

    Drives the production happy path: ``BootstrapDeps(document_root_fn=...)``
    points the use case at ``tmp_path``; the seeded files end up in the
    envelope keys ``board`` / ``active_goals`` / ``recent_memory``.

    Sabotage: mutate ``board = _load_board(agent_dir)`` →
    ``board = ""`` in ``run_bootstrap`` → ``envelope["board"]`` lands
    empty and the ``"priorities"`` assertion fails. Verified.
    """
    _seed_minimal_vault(tmp_path, "agent-alpha")

    deps = BootstrapDeps(document_root_fn=lambda: tmp_path)
    envelope = tool_bootstrap(agent="agent-alpha", max_memory_days=3, deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["agent"] == "agent-alpha", f"agent mismatch: {envelope['agent']!r}"
    assert envelope["board"].startswith("priorities"), f"board missing seeded content: {envelope['board']!r}"
    assert envelope["active_goals"], f"active_goals empty: {envelope['active_goals']!r}"
    # Goals.md seeds two bullets — both must be parsed.
    assert any("F30" in g for g in envelope["active_goals"]), (
        f"F30 goal missing from active_goals: {envelope['active_goals']!r}"
    )
    assert envelope["recent_memory"], f"recent_memory empty: {envelope['recent_memory']!r}"
    # Memory entries are dicts {date, content}.
    first_memory = envelope["recent_memory"][0]
    assert "date" in first_memory and "content" in first_memory, f"memory shape: {first_memory!r}"
    assert envelope["error"] == "", f"error should be empty for happy path: {envelope['error']!r}"


def test_tool_bootstrap_envelope_surfaces_missing_document_root(tmp_path: Path) -> None:
    """A non-existent document_root must surface as ``error`` non-empty +
    a prescriptive ``next_action`` — the agent harness keys off both.

    Sabotage: mutate ``if not root.exists()`` → ``if False`` in
    ``run_bootstrap``. The function then crashes deeper in
    ``_agent_dir`` reads (or returns a partially-filled envelope without
    the error/next-action prescription). The error assertion below fails
    because ``envelope["error"]`` lands empty. Verified.
    """
    bogus = tmp_path / "does-not-exist"
    deps = BootstrapDeps(document_root_fn=lambda: bogus)

    envelope = tool_bootstrap(agent="agent-alpha", deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["agent"] == "agent-alpha", f"agent mismatch: {envelope['agent']!r}"
    assert envelope["error"], f"error must be non-empty for missing doc root: {envelope!r}"
    assert "DocumentRootMissing" in envelope["error"], f"error class missing: {envelope['error']!r}"
    assert envelope["next_action"], f"next_action must guide the operator: {envelope!r}"
    assert "onboard check" in envelope["next_action"], (
        f"next_action must reference onboard check: {envelope['next_action']!r}"
    )
