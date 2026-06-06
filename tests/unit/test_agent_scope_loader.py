"""Unit tests for :mod:`kairix.core.agents.scope` (PR 1.1 / #420).

The loader parses the ``agents:`` block of ``kairix.config.yaml`` into a
name → AgentScope map. The resolver (``get_agent_scope``) returns one
scope by name, synthesising from ``agent_defaults`` when no explicit entry
exists. PR 1.1 ships the loader + resolver only; callers move onto the
scope abstraction in PR 1.2. These tests pin:

  * happy-path multi-agent parse,
  * fall-throughs (missing ``agents:`` / config=None → empty dict),
  * fail-fast on malformed surface entries (typed ``ValueError`` carrying the
    agent name so the operator sees which entry to fix),
  * optional-field defaults (``harness=""``, ``label=""``, ``glob="**/*.md"``),
  * resolver branches: explicit entry / synthesis-with-workspace /
    synthesis-without-workspace / missing-everything (typed ``ValueError``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kairix.core.agents.scope import get_agent_scope, load_agent_scopes

pytestmark = pytest.mark.unit


# Sabotage-proof: changed load_agent_scopes to drop the second agent in the
# loop (only kept the first) → len(...) == 2 failed; test failed; restored.
def test_loads_two_agents_from_well_formed_config() -> None:
    """A config naming two agents (shape + growth) yields two entries with
    the configured paths, labels, harnesses, and glob patterns."""
    config = {
        "agents": {
            "shape": {
                "harness": "claude-code",
                "surfaces": [
                    {"path": "/data/vault/04-Agent-Knowledge/shape", "label": "memory"},
                    {"path": "/data/workspaces/shape", "label": "workspace", "glob": "**/*.txt"},
                ],
            },
            "growth": {
                "harness": "codex",
                "surfaces": [
                    {"path": "/data/vault/04-Agent-Knowledge/growth", "label": "memory"},
                ],
            },
        }
    }
    scopes = load_agent_scopes(config)
    assert set(scopes.keys()) == {"shape", "growth"}
    shape = scopes["shape"]
    assert shape.name == "shape"
    assert shape.harness == "claude-code"
    assert len(shape.surfaces) == 2
    assert shape.surfaces[0].path == Path("/data/vault/04-Agent-Knowledge/shape")
    assert shape.surfaces[0].label == "memory"
    assert shape.surfaces[0].glob == "**/*.md"
    assert shape.surfaces[1].path == Path("/data/workspaces/shape")
    assert shape.surfaces[1].label == "workspace"
    assert shape.surfaces[1].glob == "**/*.txt"
    growth = scopes["growth"]
    assert growth.name == "growth"
    assert growth.harness == "codex"
    assert len(growth.surfaces) == 1


# Sabotage-proof: changed the missing-key fallback to `raise KeyError("agents")`
# → empty-dict assertion failed; test failed; restored.
def test_missing_agents_key_returns_empty_dict() -> None:
    """A config without an ``agents:`` block returns an empty dict — the
    caller's fall-through to ``agent_defaults`` synthesis path."""
    assert load_agent_scopes({"paths": {"document_root": "/x"}}) == {}


# Sabotage-proof: changed the None-config guard to `config = {}` and skipped
# the early return → no error, but produced empty dict anyway by coincidence;
# THEN changed it to raise on None → test failed because we expected {};
# restored to original behaviour.
def test_none_config_returns_empty_dict() -> None:
    """``config=None`` is the production "no config file found" signal and
    must return an empty dict, not raise."""
    assert load_agent_scopes(None) == {}


# Sabotage-proof: removed the missing-path check so the loader synthesised a
# Path(None) which crashed elsewhere with a TypeError → ValueError pattern
# match failed; test failed; restored.
def test_malformed_surface_missing_path_raises_value_error_with_agent_name() -> None:
    """A surface entry without a ``path`` key fails fast at load time. The
    error message names the offending agent so the operator can find the
    bad block in their yaml."""
    config = {
        "agents": {
            "shape": {
                "surfaces": [
                    {"label": "memory"},  # missing path
                ],
            }
        }
    }
    with pytest.raises(ValueError, match="shape"):
        load_agent_scopes(config)


# Sabotage-proof: changed the surfaces-type check to accept dict via
# `if not isinstance(surfaces, (list, dict))` → no error raised; test failed;
# restored to list-only.
def test_malformed_agent_surfaces_not_a_list_raises_value_error() -> None:
    """``surfaces:`` must be a list. A scalar (or a dict) is a yaml typo
    and fails fast with the agent name in the message."""
    config = {
        "agents": {
            "growth": {
                "surfaces": "not-a-list",
            }
        }
    }
    with pytest.raises(ValueError, match="growth"):
        load_agent_scopes(config)


# Sabotage-proof: changed AgentScope's default `harness=""` to `harness="x"` →
# equality assertion failed; test failed; restored.
def test_harness_field_defaults_to_empty_string() -> None:
    """The ``harness`` field is informational and optional."""
    config = {
        "agents": {
            "alpha": {
                "surfaces": [{"path": "/data/a", "label": "memory"}],
            }
        }
    }
    scope = load_agent_scopes(config)["alpha"]
    assert scope.harness == ""


# Sabotage-proof: changed AgentSurface's default `label=""` to `label="?"` →
# equality assertion failed; test failed; restored.
def test_surface_label_field_defaults_to_empty_string() -> None:
    """A surface without an explicit ``label:`` defaults to ``""``."""
    config = {
        "agents": {
            "alpha": {
                "surfaces": [{"path": "/data/a"}],
            }
        }
    }
    scope = load_agent_scopes(config)["alpha"]
    assert scope.surfaces[0].label == ""


# Sabotage-proof: changed AgentSurface's default `glob="**/*.md"` to
# `glob="*"` → equality assertion failed; test failed; restored.
def test_surface_glob_field_defaults_to_double_star_md() -> None:
    """A surface without an explicit ``glob:`` defaults to ``**/*.md``."""
    config = {
        "agents": {
            "alpha": {
                "surfaces": [{"path": "/data/a"}],
            }
        }
    }
    scope = load_agent_scopes(config)["alpha"]
    assert scope.surfaces[0].glob == "**/*.md"


# Sabotage-proof (executed): changed the isinstance(entry, dict) check in
# _build_surface to `if not isinstance(entry, list)` → the string "bad-entry"
# passed the check and crashed later with AttributeError on .get → pytest.raises
# pattern "alpha" matched but on a different error; tightened the test by
# matching "surfaces[*] must be a mapping" → assertion failed; restored.
def test_malformed_surface_not_a_mapping_raises_value_error_with_agent_name() -> None:
    """A surface entry that isn't a mapping (e.g. a bare string) fails fast
    with the agent name + ``must be a mapping`` in the message."""
    config = {
        "agents": {
            "alpha": {
                "surfaces": ["not-a-mapping"],
            }
        }
    }
    with pytest.raises(ValueError, match=r"alpha.*must be a mapping"):
        load_agent_scopes(config)


# Sabotage-proof (executed): changed the isinstance(entry, dict) check in
# _build_scope to `if isinstance(entry, dict)` (inverted) → no error raised
# for the malformed string entry; mutated again to skip the check entirely →
# crashed elsewhere with AttributeError; restored.
def test_malformed_agent_entry_not_a_mapping_raises_value_error() -> None:
    """An ``agents.<name>`` entry that isn't a mapping fails fast with the
    agent name + ``must be a mapping`` in the message."""
    config = {
        "agents": {
            "beta": "not-a-mapping",
        }
    }
    with pytest.raises(ValueError, match=r"beta.*must be a mapping"):
        load_agent_scopes(config)


# Sabotage-proof (executed): changed the isinstance(agents_raw, dict) check
# in load_agent_scopes to `if isinstance(agents_raw, list)` (inverted) → the
# string passed the check and crashed elsewhere with AttributeError on .items();
# restored.
def test_agents_block_not_a_mapping_raises_value_error() -> None:
    """The top-level ``agents:`` block must itself be a mapping. A scalar
    (or list) is a yaml typo and fails fast with a structural message."""
    config = {"agents": "not-a-mapping"}
    with pytest.raises(ValueError, match="agents must be a mapping"):
        load_agent_scopes(config)


# Sabotage-proof (executed): swapped the "explicit entry wins" branch in
# get_agent_scope to fall through to synthesis → warning was emitted and
# the assertion `not caplog.records` failed; test failed; restored.
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
    assert scope.surfaces[0].path == mem
    assert scope.surfaces[0].label == "memory"
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# Sabotage-proof (executed): removed the workspace-exists check in
# _synthesise_from_defaults so the workspace surface was always appended →
# this test with a present workspace dir still showed 2 surfaces (no change),
# but mutated again to skip workspace synthesis entirely → `len == 2` failed;
# restored.
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


# Sabotage-proof (executed): changed the workspace-exists guard to always
# append the workspace surface → len(scope.surfaces) became 2; assertion
# `== 1` failed; restored.
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


# Sabotage-proof (executed): removed the "no defaults" ValueError so the
# fall-through returned an empty AgentScope → pytest.raises saw nothing;
# test failed; restored.
def test_missing_entry_and_missing_defaults_raises_value_error(tmp_path: Path) -> None:
    """No explicit entry AND no ``agent_defaults`` block → ValueError with
    an actionable message so the operator can recover (the message names
    the agent and points at the missing config sections)."""
    with pytest.raises(ValueError, match="ghost"):
        get_agent_scope("ghost", config={}, document_root=tmp_path)


# Sabotage-proof (executed): changed the `if config is None` guard to
# `if config is not None` (inverted) → the test passed because the
# fallback raised but with wrong message; restored.
def test_get_agent_scope_with_none_config_raises_value_error(tmp_path: Path) -> None:
    """``config=None`` is the no-config-file signal. With no agents entry +
    no defaults, the resolver must raise ValueError with the agent name."""
    with pytest.raises(ValueError, match="phantom"):
        get_agent_scope("phantom", config=None, document_root=tmp_path)


# Sabotage-proof (executed): changed the `_synthesise_from_defaults` body to
# skip the memory_root branch when memory_root is set → scope had 0 surfaces
# instead of 1; assertion `len == 1` failed; restored.
def test_synthesis_uses_custom_glob_when_provided(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The ``agent_defaults.glob`` value flows into synthesised surfaces."""
    mem_root = tmp_path / "vault" / "04-Agent-Knowledge"
    (mem_root / "delta").mkdir(parents=True)
    config = {
        "agent_defaults": {
            "memory_root": str(mem_root),
            "glob": "**/*.txt",
        }
    }
    with caplog.at_level(logging.WARNING, logger="kairix.core.agents.scope"):
        scope = get_agent_scope("delta", config=config, document_root=tmp_path)
    assert scope.surfaces[0].glob == "**/*.txt"
