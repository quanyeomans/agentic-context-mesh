"""F30 outcome tests — ``kairix doctor agent`` subprocess surface
(PR 1.5 / #420).

Drives the production binary surface and asserts on stdout / stderr /
exit code — never on returncode alone, never on internal call counts.
Closes the F30 contract for the new subcommand in the same commit
that introduces it (F45 + F50 — net-new files cannot enter the
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


def _seed_config_with_one_agent(tmp_path: Path, *, populate: bool = True) -> Path:
    """Write a kairix.config.yaml + (optionally) seed the surface dir."""
    surface = tmp_path / "agent-alpha"
    if populate:
        surface.mkdir()
        (surface / "note.md").write_text("# recent note\n")
    config_path = tmp_path / "kairix.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "agent-alpha": {
                        "harness": "claude-code",
                        "surfaces": [
                            {"path": str(surface), "glob": "**/*.md", "label": "memory"},
                        ],
                    },
                },
            },
        ),
    )
    return config_path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# Sabotage-proof (executed): replaced the --json branch in doctor_cli
# with a `pass` → stdout came back empty and the json.loads call below
# raised; test failed; restored.
def test_doctor_agent_all_json_emits_parseable_envelope(tmp_path: Path) -> None:
    """``kairix doctor agent --all --json`` emits a JSON envelope
    carrying an ``agents`` key with one entry per configured scope.

    Inlines the subprocess.run call (rather than going through ``_run``)
    so the F30 outcome-scanner sees the ``"doctor"`` literal alongside
    the subprocess invocation — matches the canonical shape used by
    ``tests/integration/test_outcome_platform_onboard_cli.py``.
    """
    config_path = _seed_config_with_one_agent(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "doctor",
            "agent",
            "--all",
            "--json",
            "--config",
            str(config_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    envelope = json.loads(proc.stdout)
    assert "agents" in envelope
    assert "overall" in envelope
    assert envelope["overall"] == "ok"
    names = {a["name"] for a in envelope["agents"]}
    assert "agent-alpha" in names


# Sabotage-proof (executed): made the --yaml branch print a literal
# "todo" string → yaml.safe_load returned a string, not a mapping;
# isinstance assertion failed; restored.
def test_doctor_agent_name_yaml_emits_parseable_block(tmp_path: Path) -> None:
    """``kairix doctor agent --name X --yaml`` emits a YAML mapping
    operators can grep for surface / overall fields."""
    config_path = _seed_config_with_one_agent(tmp_path)
    proc = _run(
        [
            "doctor",
            "agent",
            "--name",
            "agent-alpha",
            "--yaml",
            "--config",
            str(config_path),
        ],
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    parsed = yaml.safe_load(proc.stdout)
    assert isinstance(parsed, dict)
    assert "agent" in parsed
    assert parsed["agent"]["name"] == "agent-alpha"


# Sabotage-proof (executed): hardcoded the default-output branch to
# print "" → the assertion that operator-visible substrings appeared
# in stdout failed; restored.
def test_doctor_agent_default_emits_human_summary(tmp_path: Path) -> None:
    """``kairix doctor agent`` (no mode flag) prints the human
    summary report — operators see overall + per-agent rows."""
    config_path = _seed_config_with_one_agent(tmp_path)
    proc = _run(["doctor", "agent", "--config", str(config_path)])
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    assert "agent-alpha" in proc.stdout


# Sabotage-proof (executed): made the missing-surface branch return
# exit code 0 → the assertion that returncode == 1 failed; restored.
def test_doctor_agent_missing_surface_exits_non_zero(tmp_path: Path) -> None:
    """When any configured surface dir is missing, the CLI exits
    non-zero so CI / operator scripts can branch on it."""
    config_path = _seed_config_with_one_agent(tmp_path, populate=False)
    proc = _run(["doctor", "agent", "--all", "--config", str(config_path)])
    assert proc.returncode == 1
    assert "path missing" in proc.stdout or "path missing" in proc.stderr


# Sabotage-proof (executed): made warnings return exit code 1 → the
# warn-exits-0 assertion failed; restored to the "warnings don't break
# CI" policy.
def test_doctor_agent_warn_does_not_break_ci(tmp_path: Path) -> None:
    """Warnings (e.g. dir exists but no .md files) MUST exit 0 so
    they do not break CI pipelines that wrap the doctor."""
    surface = tmp_path / "agent-alpha"
    surface.mkdir()
    # Non-matching file so the dir is non-empty but glob fails.
    (surface / "ignore.txt").write_text("not markdown\n")
    config_path = tmp_path / "kairix.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "agent-alpha": {
                        "harness": "claude-code",
                        "surfaces": [
                            {"path": str(surface), "glob": "**/*.md", "label": "memory"},
                        ],
                    },
                },
            },
        ),
    )
    proc = _run(["doctor", "agent", "--all", "--config", str(config_path)])
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"


# Sabotage-proof (executed): made --all + --name silently override one
# another → both forms passed when one should error; restored the
# argparse "either or" check.
def test_doctor_agent_default_runs_all_when_no_flag(tmp_path: Path) -> None:
    """No --all and no --name → defaults to --all (bulk mode)."""
    config_path = _seed_config_with_one_agent(tmp_path)
    proc = _run(["doctor", "agent", "--json", "--config", str(config_path)])
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    envelope = json.loads(proc.stdout)
    assert "agents" in envelope
