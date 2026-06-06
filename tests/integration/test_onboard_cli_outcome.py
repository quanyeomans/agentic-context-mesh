"""F30 outcome tests — ``kairix onboard scan`` + ``kairix onboard agent``
subprocess surface (PR 1.4 / #420).

Drives the production binary surface and asserts on stdout / stderr /
exit code — never on returncode alone, never on internal call counts.
Closes the F30 contract for both new subcommands in the same commit
that introduces them (F45 + F50 — net-new files cannot enter the
baseline).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped] — PyYAML ships without type stubs upstream

pytestmark = pytest.mark.integration


def _seed_memory_root(tmp_path: Path) -> Path:
    memory = tmp_path / "memory"
    memory.mkdir()
    alpha = memory / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory\n")
    (alpha / "note.md").write_text("# note\n")
    beta = memory / "agent-beta"
    beta.mkdir()
    (beta / "Board.md").write_text("# board\n")
    return memory


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# Sabotage-proof (executed): replaced the `--json` flag handler in the
# new onboarding CLI with a `pass` → stdout came back empty and the
# `json.loads` call below raised; test failed; restored.
def test_onboard_scan_json_emits_parseable_envelope(tmp_path: Path) -> None:
    """``kairix onboard scan --json`` emits a JSON envelope carrying
    an ``agents`` key with one entry per discovered scope."""
    memory = _seed_memory_root(tmp_path)
    proc = _run(["onboard", "scan", "--memory-root", str(memory), "--json"])
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    envelope = json.loads(proc.stdout)
    assert "agents" in envelope
    names = {a["name"] for a in envelope["agents"]}
    assert names == {"agent-alpha", "agent-beta"}


# Sabotage-proof (executed): made the YAML branch print a literal
# "todo" string → yaml.safe_load returned a string, not a mapping;
# isinstance assertion failed; restored.
def test_onboard_scan_yaml_emits_parseable_block(tmp_path: Path) -> None:
    """``kairix onboard scan --yaml`` emits a YAML mapping operators
    can paste verbatim into kairix.config.yaml."""
    memory = _seed_memory_root(tmp_path)
    proc = _run(["onboard", "scan", "--memory-root", str(memory), "--yaml"])
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    parsed = yaml.safe_load(proc.stdout)
    assert isinstance(parsed, dict)
    assert "agents" in parsed
    assert "agent-alpha" in parsed["agents"]


# Sabotage-proof (executed): hardcoded the default-output branch to
# print "" → the assertion that operator-visible substrings appeared
# in stdout failed; restored.
def test_onboard_scan_default_emits_validation_report(tmp_path: Path) -> None:
    """``kairix onboard scan`` (no mode flag) prints the validation
    report — operators see file count + most-recent-mtime per scope."""
    memory = _seed_memory_root(tmp_path)
    proc = _run(["onboard", "scan", "--memory-root", str(memory)])
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    assert "agent-alpha" in proc.stdout
    assert "agent-beta" in proc.stdout


# Sabotage-proof (executed): removed the `--name` argparse plumbing →
# the subcommand could not parse `--name` and exited 2; test failed on
# the returncode assertion; restored.
def test_onboard_agent_json_emits_single_envelope(tmp_path: Path) -> None:
    """``kairix onboard agent --name X --json`` emits a JSON envelope
    with one ``agent`` block, not the bulk-scan ``agents`` list."""
    memory = _seed_memory_root(tmp_path)
    proc = _run(
        [
            "onboard",
            "agent",
            "--name",
            "agent-alpha",
            "--memory-root",
            str(memory),
            "--json",
        ],
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    envelope = json.loads(proc.stdout)
    assert envelope["agent"]["name"] == "agent-alpha"


# Sabotage-proof (executed): made the unknown-agent branch return
# exit code 0 → the assertion that returncode == 1 failed;
# restored.
def test_onboard_agent_unknown_exits_non_zero_with_actionable_stderr(
    tmp_path: Path,
) -> None:
    """``kairix onboard agent`` for a missing agent name exits non-
    zero and names the missing agent on stderr."""
    memory = _seed_memory_root(tmp_path)
    proc = _run(
        [
            "onboard",
            "agent",
            "--name",
            "ghost-agent",
            "--memory-root",
            str(memory),
        ],
    )
    assert proc.returncode != 0
    assert "ghost-agent" in proc.stderr
