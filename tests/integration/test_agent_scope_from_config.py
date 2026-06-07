"""Integration tests for :func:`kairix.core.agents.scope.get_agent_scope`
(PR 1.1 / #420).

The resolver returns an :class:`AgentScope` for a given agent name. When the
config has an explicit ``agents.<name>`` entry it is returned verbatim; when
the entry is missing, the loader synthesises a scope from the
``agent_defaults`` block — emitting a one-line warning so operators see the
drift and can commit explicit config via ``kairix onboard agent``.

These tests exercise the public ``get_agent_scope(...)`` surface end-to-end
(F46 / F47-clean — no internal symbol imports, no monkeypatching). The
``config`` + ``document_root`` kwargs are the documented test seams.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kairix.core.agents.scope import get_agent_scope

pytestmark = pytest.mark.integration


# Sabotage-proof: swapped the "explicit entry wins" branch to fall through to
# synthesis → warning was emitted and the assertion `not caplog.records`
# failed; test failed; restored.
def test_explicit_config_entry_returns_configured_scope_without_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When an agent has an explicit ``agents.<name>`` block, the resolver
    returns that scope verbatim and emits no warning."""
    mem = tmp_path / "vault" / "04-Agent-Knowledge" / "shape"
    mem.mkdir(parents=True)
    config = {
        "agents": {
            "shape": {
                "harness": "claude-code",
                "surfaces": [
                    {"path": str(mem), "label": "memory"},
                ],
            }
        }
    }
    with caplog.at_level(logging.WARNING, logger="kairix.core.agents.scope"):
        scope = get_agent_scope("shape", config=config, document_root=tmp_path)
    assert scope.name == "shape"
    assert scope.harness == "claude-code"
    assert scope.surfaces == (scope.surfaces[0],)
    assert scope.surfaces[0].path == mem
    assert scope.surfaces[0].label == "memory"
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# Sabotage-proof: removed the workspace-exists check so synthesis always added
# the workspace surface even when the directory was missing → assertion that
# both surfaces appear succeeded only because the dir DID exist; mutated again
# to skip workspace synthesis entirely → assertion failed; restored.
def test_missing_entry_synthesises_two_surfaces_when_workspace_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No explicit entry + an ``agent_defaults`` block + a present
    workspace directory → synthesise (memory, workspace) and warn."""
    mem_root = tmp_path / "vault" / "04-Agent-Knowledge"
    work_root = tmp_path / "workspaces"
    (mem_root / "shape").mkdir(parents=True)
    (work_root / "shape").mkdir(parents=True)
    config = {
        "agent_defaults": {
            "memory_root": str(mem_root),
            "workspace_root": str(work_root),
            "glob": "**/*.md",
        }
    }
    with caplog.at_level(logging.WARNING, logger="kairix.core.agents.scope"):
        scope = get_agent_scope("shape", config=config, document_root=tmp_path)
    assert scope.name == "shape"
    assert len(scope.surfaces) == 2
    assert scope.surfaces[0].path == mem_root / "shape"
    assert scope.surfaces[0].label == "memory"
    assert scope.surfaces[1].path == work_root / "shape"
    assert scope.surfaces[1].label == "workspace"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "shape" in warnings[0].getMessage()


# Sabotage-proof: changed the workspace-exists guard to always append the
# workspace surface → len(scope.surfaces) became 2; assertion `== 1` failed;
# restored.
def test_missing_entry_synthesises_memory_only_when_workspace_absent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No explicit entry, ``agent_defaults`` set, but the workspace dir does
    not exist on disk → synthesise memory-only and warn."""
    mem_root = tmp_path / "vault" / "04-Agent-Knowledge"
    work_root = tmp_path / "workspaces"  # never created
    (mem_root / "growth").mkdir(parents=True)
    config = {
        "agent_defaults": {
            "memory_root": str(mem_root),
            "workspace_root": str(work_root),
        }
    }
    with caplog.at_level(logging.WARNING, logger="kairix.core.agents.scope"):
        scope = get_agent_scope("growth", config=config, document_root=tmp_path)
    assert len(scope.surfaces) == 1
    assert scope.surfaces[0].path == mem_root / "growth"
    assert scope.surfaces[0].label == "memory"


# Sabotage-proof (executed): broke the document_root fallback path → the
# returned scope's surface pointed at the wrong path; assertion failed; restored.
def test_missing_entry_and_missing_defaults_falls_back_to_document_root(
    tmp_path: Path,
) -> None:
    """No explicit entry AND no ``agent_defaults`` block → built-in fallback
    to ``{document_root}/04-Agent-Knowledge/<name>`` so kairix works
    out-of-the-box without explicit config. The operator still sees a
    warning naming the agent + onboard command for committing explicit config."""
    scope = get_agent_scope("ghost", config={}, document_root=tmp_path)
    assert len(scope.surfaces) == 1
    assert scope.surfaces[0].path == tmp_path / "04-Agent-Knowledge" / "ghost"
