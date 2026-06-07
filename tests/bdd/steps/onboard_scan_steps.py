"""Step definitions for onboard_scan_discovers_agents.feature.

Drives ``kairix onboard scan`` + ``kairix onboard agent`` as subprocesses
so the F30 / F45 / F46 contract is met: BDD composes the production
CLI binary, not in-process helpers.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped] — PyYAML ships without type stubs upstream
from pytest_bdd import given, parsers, then, when


@dataclass
class _OnboardScanCtx:
    memory_root: Path = Path()
    workspace_root: Path = Path()
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    parsed_yaml: dict[str, object] = field(default_factory=dict)


@pytest.fixture
def onboard_scan_ctx(tmp_path: Path) -> _OnboardScanCtx:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    return _OnboardScanCtx(memory_root=memory_root, workspace_root=workspace_root)


@given("an empty memory root and an empty workspace root")
def _given_empty_roots(onboard_scan_ctx: _OnboardScanCtx) -> None:
    # Roots are created by the fixture already; this Given is purely
    # narrative — pin the precondition for the scenario reader.
    assert onboard_scan_ctx.memory_root.is_dir()
    assert onboard_scan_ctx.workspace_root.is_dir()


@given(parsers.parse('an agent named "{name}" with a CLAUDE.md and three markdown files in the memory root'))
def _given_claude_agent(onboard_scan_ctx: _OnboardScanCtx, name: str) -> None:
    agent = onboard_scan_ctx.memory_root / name
    agent.mkdir()
    (agent / "CLAUDE.md").write_text("# memory\n")
    for i in range(3):
        (agent / f"note-{i}.md").write_text(f"# note {i}\n")


@given(parsers.parse('an agent named "{name}" with a Board.md and five markdown files in the memory root'))
def _given_generic_agent(onboard_scan_ctx: _OnboardScanCtx, name: str) -> None:
    agent = onboard_scan_ctx.memory_root / name
    agent.mkdir()
    (agent / "Board.md").write_text("# board\n")
    for i in range(5):
        (agent / f"entry-{i}.md").write_text(f"# entry {i}\n")


@given(parsers.parse('an "{name}" workspace subdir exists in the workspace root'))
def _given_workspace_subdir(onboard_scan_ctx: _OnboardScanCtx, name: str) -> None:
    workspace = onboard_scan_ctx.workspace_root / name
    workspace.mkdir()
    (workspace / "workspace-note.md").write_text("# ws\n")


def _run_subprocess(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


@when("the operator runs the onboard scan CLI with the YAML mode")
def _when_run_scan_yaml(onboard_scan_ctx: _OnboardScanCtx) -> None:
    code, out, err = _run_subprocess(
        [
            "onboard",
            "scan",
            "--memory-root",
            str(onboard_scan_ctx.memory_root),
            "--workspace-root",
            str(onboard_scan_ctx.workspace_root),
            "--yaml",
        ],
    )
    onboard_scan_ctx.exit_code = code
    onboard_scan_ctx.stdout = out
    onboard_scan_ctx.stderr = err
    if out.strip():
        try:
            onboard_scan_ctx.parsed_yaml = yaml.safe_load(out) or {}
        except yaml.YAMLError:
            onboard_scan_ctx.parsed_yaml = {}


@when(parsers.parse('the operator runs the onboard agent CLI for "{name}"'))
def _when_run_agent_unknown(
    onboard_scan_ctx: _OnboardScanCtx,
    name: str,
) -> None:
    code, out, err = _run_subprocess(
        [
            "onboard",
            "agent",
            "--name",
            name,
            "--memory-root",
            str(onboard_scan_ctx.memory_root),
        ],
    )
    onboard_scan_ctx.exit_code = code
    onboard_scan_ctx.stdout = out
    onboard_scan_ctx.stderr = err


@then(parsers.parse("the onboard scan CLI exits with status {code:d}"))
def _then_exit_code(onboard_scan_ctx: _OnboardScanCtx, code: int) -> None:
    assert onboard_scan_ctx.exit_code == code, (
        f"expected exit {code}, got {onboard_scan_ctx.exit_code}; "
        f"stdout={onboard_scan_ctx.stdout[:400]!r} "
        f"stderr={onboard_scan_ctx.stderr[:400]!r}"
    )


@then('the YAML output carries an "agents" top-level key')
def _then_yaml_has_agents_key(onboard_scan_ctx: _OnboardScanCtx) -> None:
    assert "agents" in onboard_scan_ctx.parsed_yaml, (
        f"yaml missing 'agents' key: {onboard_scan_ctx.parsed_yaml!r}; raw stdout={onboard_scan_ctx.stdout[:400]!r}"
    )


@then(parsers.parse('the YAML output names the agent "{name}" with harness "{harness}"'))
def _then_yaml_names_agent(
    onboard_scan_ctx: _OnboardScanCtx,
    name: str,
    harness: str,
) -> None:
    agents = onboard_scan_ctx.parsed_yaml.get("agents")
    assert isinstance(agents, dict), f"agents not a mapping: {agents!r}"
    block = agents.get(name)
    assert isinstance(block, dict), f"agent {name!r} missing: {agents!r}"
    assert block.get("harness") == harness, (
        f"agent {name!r} harness mismatch: expected {harness!r}, got {block.get('harness')!r}"
    )


@then("stderr from the onboard scan CLI names the missing agent")
def _then_stderr_names_missing(onboard_scan_ctx: _OnboardScanCtx) -> None:
    err = onboard_scan_ctx.stderr.lower()
    assert "nonexistent" in err, f"stderr did not name the missing agent: {onboard_scan_ctx.stderr!r}"
