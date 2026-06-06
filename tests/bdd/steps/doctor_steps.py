"""Step definitions for cli_doctor.feature (PR 1.5 / #420).

Drives ``kairix doctor agent`` as a subprocess so the F30 / F45 / F46
contract is met: BDD composes the production CLI binary, not in-process
helpers.

The doctor command loads its scope config from a ``--config`` YAML file
passed on the command line. These steps seed a small temp config and
matching disk layout, run the CLI, and assert on stdout / stderr /
exit code.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped] — PyYAML ships without type stubs upstream
from pytest_bdd import given, parsers, then, when


@dataclass
class _DoctorCtx:
    config_path: Path = Path()
    surface_root: Path = Path()
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    parsed_envelope: dict[str, object] = field(default_factory=dict)


@pytest.fixture
def doctor_ctx(tmp_path: Path) -> _DoctorCtx:
    """Per-scenario fixture — every scenario gets its own tmp config."""
    return _DoctorCtx(
        config_path=tmp_path / "kairix.config.yaml",
        surface_root=tmp_path / "surfaces",
    )


def _write_config(doctor_ctx: _DoctorCtx, agents: dict[str, object]) -> None:
    doctor_ctx.config_path.write_text(yaml.safe_dump({"agents": agents}))


@given(parsers.parse('a configured agent "{name}" with a populated recent surface'))
def _given_populated_agent(doctor_ctx: _DoctorCtx, name: str) -> None:
    agent_dir = doctor_ctx.surface_root / name
    agent_dir.mkdir(parents=True)
    (agent_dir / "note.md").write_text("# recent note\n")
    _write_config(
        doctor_ctx,
        {
            name: {
                "harness": "claude-code",
                "surfaces": [
                    {"path": str(agent_dir), "glob": "**/*.md", "label": "memory"},
                ],
            },
        },
    )


@given(parsers.parse('a configured agent "{name}" whose surface path does not exist'))
def _given_missing_surface_agent(doctor_ctx: _DoctorCtx, name: str) -> None:
    ghost = doctor_ctx.surface_root / "no-such-dir"
    _write_config(
        doctor_ctx,
        {
            name: {
                "harness": "claude-code",
                "surfaces": [
                    {"path": str(ghost), "glob": "**/*.md", "label": "memory"},
                ],
            },
        },
    )


def _run_subprocess(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


@when("the operator runs the doctor agent CLI with the --all flag")
def _when_run_all(doctor_ctx: _DoctorCtx) -> None:
    code, out, err = _run_subprocess(
        [
            "doctor",
            "agent",
            "--all",
            "--config",
            str(doctor_ctx.config_path),
        ],
    )
    doctor_ctx.exit_code = code
    doctor_ctx.stdout = out
    doctor_ctx.stderr = err


@when(parsers.parse('the operator runs the doctor agent CLI for "{name}" with --json'))
def _when_run_single_json(doctor_ctx: _DoctorCtx, name: str) -> None:
    code, out, err = _run_subprocess(
        [
            "doctor",
            "agent",
            "--name",
            name,
            "--json",
            "--config",
            str(doctor_ctx.config_path),
        ],
    )
    doctor_ctx.exit_code = code
    doctor_ctx.stdout = out
    doctor_ctx.stderr = err
    if out.strip():
        try:
            doctor_ctx.parsed_envelope = json.loads(out)
        except json.JSONDecodeError:
            doctor_ctx.parsed_envelope = {}


@then(parsers.parse("the doctor CLI exits with status {code:d}"))
def _then_exit_code(doctor_ctx: _DoctorCtx, code: int) -> None:
    assert doctor_ctx.exit_code == code, (
        f"expected exit {code}, got {doctor_ctx.exit_code}; "
        f"stdout={doctor_ctx.stdout[:400]!r} "
        f"stderr={doctor_ctx.stderr[:400]!r}"
    )


@then(parsers.parse('stdout from the doctor CLI mentions "{phrase}"'))
def _then_stdout_mentions(doctor_ctx: _DoctorCtx, phrase: str) -> None:
    assert phrase in doctor_ctx.stdout, f"expected substring {phrase!r} in stdout; got {doctor_ctx.stdout!r}"


@then(parsers.parse('stdout from the doctor CLI carries the "{phrase}" issue'))
def _then_stdout_issue(doctor_ctx: _DoctorCtx, phrase: str) -> None:
    assert phrase in doctor_ctx.stdout, f"expected {phrase!r} in stdout; got {doctor_ctx.stdout!r}"


@then(parsers.parse('stdout from the doctor CLI suggests "{phrase}"'))
def _then_stdout_suggests(doctor_ctx: _DoctorCtx, phrase: str) -> None:
    assert phrase in doctor_ctx.stdout, f"expected suggestion {phrase!r} in stdout; got {doctor_ctx.stdout!r}"


@then('the JSON envelope carries an "agent" key with the expected fields')
def _then_envelope_shape(doctor_ctx: _DoctorCtx) -> None:
    envelope = doctor_ctx.parsed_envelope
    assert "agent" in envelope, f"envelope missing 'agent': {envelope!r}"
    agent = envelope["agent"]
    assert isinstance(agent, dict), f"agent not a mapping: {agent!r}"
    for required in ("name", "harness", "surfaces", "overall"):
        assert required in agent, f"agent missing {required!r}: {agent!r}"
