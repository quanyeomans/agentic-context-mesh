"""Unit tests for ``tool_memory_write`` — the agent-facing memory-write MCP tool (#472).

Pins the contract from the agent's point of view:

  - Happy path writes the dated markdown file and returns the envelope
    ``{path, agent, kind, classified_as, indexed, error, detail}``.
  - An agent missing from the config allowlist is rejected with the F21
    ``fix:`` / ``next:`` affordance and nothing is written.
  - Missing content is rejected before any I/O.
  - Invalid kind is rejected with actionable guidance.

F1-clean: every collaborator is injected through the ``deps=`` seam
(``RememberDeps`` over tmp paths) — no monkeypatching, no env vars.
The tool wraps the SAME use case as ``kairix remember`` (one
implementation, two surfaces).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.agents.mcp.tools.memory_write import tool_memory_write
from kairix.use_cases.remember import RememberDeps

pytestmark = pytest.mark.unit

_FIXED_NOW = datetime(2026, 6, 11, 9, 30, tzinfo=timezone.utc)

_AGENT_ALPHA_CONFIG: dict[str, object] = {
    "agents": {
        "agent-alpha": {
            "harness": "claude-code",
            "surfaces": [{"path": "04-Agent-Knowledge/agent-alpha", "label": "memory"}],
        }
    }
}


def _deps(tmp_path: Path, *, config: dict[str, object] | None = None, indexed: bool = True) -> RememberDeps:
    cfg = config if config is not None else _AGENT_ALPHA_CONFIG
    return RememberDeps(
        config_fn=lambda: cfg,
        document_root_fn=lambda: tmp_path / "vault",
        db_path_fn=lambda: tmp_path / "index.sqlite",
        now_fn=lambda: _FIXED_NOW,
        index_fn=lambda _db, _root, _hash: indexed,
    )


def test_happy_path_writes_memory_and_returns_envelope(tmp_path: Path) -> None:
    """A configured agent's note is written to disk and the envelope
    carries every contract key.

    Sabotage: replace the ``remember(...)`` call in tool_memory_write
    with a hand-rolled dict → the file-exists assertion fails.
    """
    out = tool_memory_write(
        agent="agent-alpha",
        content="rule: always check the board before starting work",
        deps=_deps(tmp_path),
    )

    assert out["error"] == ""
    assert out["agent"] == "agent-alpha"
    assert out["kind"] == "note"
    assert out["classified_as"] == "procedural-rule"
    assert out["indexed"] is True

    written = Path(out["path"])
    assert written.exists(), f"expected memory file at {written}"
    assert written.parent == tmp_path / "vault" / "04-Agent-Knowledge" / "agent-alpha"
    assert "always check the board" in written.read_text(encoding="utf-8")


def test_invalid_agent_is_rejected_with_f21_envelope(tmp_path: Path) -> None:
    """An agent outside the configured union legacy allowlist gets the
    InvalidAgent envelope with fix:/next: markers; the vault stays empty.

    Sabotage: remove the allowlist check from the remember use case →
    ``error`` is "" and the no-file assertion fails.
    """
    out = tool_memory_write(
        agent="agent-omega",
        content="anything",
        deps=_deps(tmp_path),
    )

    assert out["error"].startswith("InvalidAgent:")
    assert "agent-omega" in out["error"]
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in out["error"]
    assert "next: re-run kairix doctor agent --all" in out["error"]
    assert out["path"] == ""
    assert not (tmp_path / "vault").exists()


def test_missing_content_is_rejected_before_any_io(tmp_path: Path) -> None:
    """Empty content short-circuits with EmptyContent; nothing is written.

    Sabotage: remove the empty-content guard from the remember use case
    → a file appears under the vault and both assertions fail.
    """
    out = tool_memory_write(agent="agent-alpha", content="", deps=_deps(tmp_path))

    assert out["error"].startswith("EmptyContent:")
    assert "fix:" in out["error"]
    assert out["path"] == ""
    assert not (tmp_path / "vault").exists()


def test_invalid_kind_is_rejected_with_actionable_error(tmp_path: Path) -> None:
    """A kind outside note|decision|fact is rejected with guidance.

    Sabotage: remove the kind guard from the remember use case → the
    call succeeds and the error assertion fails.
    """
    out = tool_memory_write(
        agent="agent-alpha",
        content="some text",
        kind="poem",
        deps=_deps(tmp_path),
    )

    assert out["error"].startswith("InvalidKind:")
    assert "fix:" in out["error"]
    assert out["path"] == ""


def test_not_indexed_outcome_carries_reindex_affordance(tmp_path: Path) -> None:
    """When immediate indexing reports False the envelope still carries
    the saved path plus the ``next: run kairix embed`` affordance.

    Sabotage: drop the ``detail`` population for the not-indexed branch
    in the remember use case → the affordance assertion fails.
    """
    out = tool_memory_write(
        agent="agent-alpha",
        content="decided: ship it",
        kind="decision",
        deps=_deps(tmp_path, indexed=False),
    )

    assert out["error"] == ""
    assert out["indexed"] is False
    assert "next: run kairix embed" in out["detail"]
    assert Path(out["path"]).exists()
