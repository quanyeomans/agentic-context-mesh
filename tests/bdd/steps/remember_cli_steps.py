"""Step definitions for cli_remember.feature (#472).

F46-clean: every scenario composes through the public CLI surface
(``kairix.use_cases.remember.main``) with deps injected through the
``RememberDeps`` seam — no direct pipeline construction, no
monkeypatching (F1), no env vars (F2). F13-clean: scenarios speak in
agent/memory language, never implementation symbols.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.use_cases.remember import RememberDeps
from kairix.use_cases.remember import main as remember_main

pytestmark = pytest.mark.bdd

_BDD_NOW = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)


@dataclass
class _RememberState:
    """Per-scenario state — fresh on every scenario."""

    document_root: Path
    db_path: Path
    config: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _remember_state(tmp_path: Path) -> _RememberState:
    return _RememberState(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "index.sqlite",
    )


def _deps_from(state: _RememberState) -> RememberDeps:
    return RememberDeps(
        config_fn=lambda: state.config,
        document_root_fn=lambda: state.document_root,
        db_path_fn=lambda: state.db_path,
        now_fn=lambda: _BDD_NOW,
    )


def _run_remember(state: _RememberState, agent: str, content: str, kind: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    state.exit_code = remember_main(
        [agent, content, "--kind", kind, "--json"],
        out=out,
        err=err,
        deps=_deps_from(state),
    )
    state.stdout = out.getvalue()
    state.stderr = err.getvalue()
    state.envelope = json.loads(state.stdout) if state.stdout.strip() else {}


@given("agent-alpha is declared in the team's agent configuration")
def _agent_alpha_configured(_remember_state: _RememberState) -> None:
    _remember_state.config = {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [{"path": "04-Agent-Knowledge/agent-alpha", "label": "memory"}],
            }
        }
    }


@given("no agent configuration exists")
def _no_agent_config(_remember_state: _RememberState) -> None:
    _remember_state.config = {}


@when(parsers.parse('agent-alpha remembers the decision "{content}"'))
def _alpha_remembers(_remember_state: _RememberState, content: str) -> None:
    _run_remember(_remember_state, "agent-alpha", content, kind="decision")


@when(parsers.parse('agent builder remembers the decision "{content}"'))
def _builder_remembers(_remember_state: _RememberState, content: str) -> None:
    _run_remember(_remember_state, "builder", content, kind="decision")


@when(parsers.parse('agent-omega tries to remember "{content}"'))
def _omega_remembers(_remember_state: _RememberState, content: str) -> None:
    _run_remember(_remember_state, "agent-omega", content, kind="note")


@then(parsers.parse("the memory is saved as a dated file under {agent}'s memory area"))
def _then_saved_under_agent(_remember_state: _RememberState, agent: str) -> None:
    saved = Path(_remember_state.envelope["path"])
    assert saved.exists(), f"expected saved memory at {saved}"
    expected_dir = _remember_state.document_root / "04-Agent-Knowledge" / agent
    assert saved.parent == expected_dir, f"expected {saved} under {expected_dir}"
    assert saved.name.startswith(_BDD_NOW.date().isoformat()), f"expected dated filename, got {saved.name}"


@then("the saved memory is reported as a decision")
def _then_kind_decision(_remember_state: _RememberState) -> None:
    assert _remember_state.envelope["kind"] == "decision"


@then("the remember response reports no error")
def _then_no_error(_remember_state: _RememberState) -> None:
    assert _remember_state.exit_code == 0, f"stderr: {_remember_state.stderr!r}"
    assert _remember_state.envelope["error"] == ""


@then("the remember response is an error naming agent-omega")
def _then_error_names_agent(_remember_state: _RememberState) -> None:
    assert _remember_state.exit_code == 1
    assert "agent-omega" in _remember_state.stderr


@then("the error tells the operator to add the agent to the configuration")
def _then_error_has_affordance(_remember_state: _RememberState) -> None:
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in _remember_state.stderr
