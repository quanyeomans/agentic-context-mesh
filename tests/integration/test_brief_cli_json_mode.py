"""F30 outcome test — ``kairix brief --json`` subprocess surface.

PR 2.1 / #421 wired a ``--json`` flag on ``kairix brief`` so the warm
MCP path (PR 2.8) can route text-mode invocations through the envelope
shape. This test exercises the subprocess binary surface for both modes:

- ``kairix brief --json <agent>`` → stdout is a JSON envelope dict
  (``content`` / ``path`` / ``error`` keys), exit 0 on degraded happy
  path.
- ``kairix brief <agent>`` (no ``--json``) → text-mode output unchanged
  from pre-PR-2.1 behaviour. Regression net against the dispatcher
  flip-over breaking text mode.

PR 1.2 / #420 — the legacy ``--memory-root`` flag has been removed.
Tests now seed a ``kairix.config.yaml`` in ``tmp_path`` and run the
subprocess with ``cwd=tmp_path`` so the cwd-relative config is picked
up — the F2-clean replacement for the old per-subprocess env-var hack.

The subprocess env strips ``KAIRIX_LLM_API_KEY`` so the health probe
takes the deterministic ``chat=offline`` branch — the test doesn't
depend on the developer's shell having a real LLM key. This is NOT
``monkeypatch.setenv`` (F2 targets pytest monkeypatch); passing an
explicit ``env=`` dict to ``subprocess.run`` is the F2-clean way to
control a child process's environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed_minimal_brief_workspace(tmp_path: Path, agent: str) -> None:
    """Seed a ``kairix.config.yaml`` + the agent surface the brief reads.

    Mirrors the helper in ``test_outcome_briefing_cli.py`` — operators
    who used to pass ``--memory-root`` now declare the directory under
    ``agents.<name>.surfaces`` in ``kairix.config.yaml``.
    """
    agent_dir = tmp_path / agent
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_yaml = tmp_path / "kairix.config.yaml"
    config_yaml.write_text(
        textwrap.dedent(f"""\
            agents:
              {agent}:
                surfaces:
                  - path: {agent_dir}
                    label: memory
            """),
        encoding="utf-8",
    )


def _subprocess_env_without_llm_keys() -> dict[str, str]:
    """Return a subprocess env that strips LLM credential vars."""
    env = dict(os.environ)
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


# Sabotage-proof (executed): mutated ``main()`` in
# ``kairix/agents/briefing/cli.py`` to emit ``print("not json")``
# instead of ``json.dumps(envelope)`` under the ``--json`` branch — the
# ``json.loads(proc.stdout)`` assertion fired with JSONDecodeError;
# restored.
def test_brief_cli_subprocess_json_mode_emits_envelope_dict(tmp_path: Path) -> None:
    """Drive the real ``kairix brief --json`` binary against a tmp config.

    Asserts stdout parses as JSON and carries the envelope-shape keys
    (F30: outcome on stdout, not just returncode). Degraded health
    (no LLM cred) still produces a well-formed envelope — that's the
    contract the MCP dispatcher will rely on once PR 2.8 lands.
    """
    _seed_minimal_brief_workspace(tmp_path, "builder")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "brief",
            "builder",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env_without_llm_keys(),
        cwd=str(tmp_path),
        check=False,
    )

    assert proc.returncode == 0, (
        f"brief --json exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    for key in ("agent", "content", "path", "preview", "health", "error"):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert envelope["agent"] == "builder"


# Sabotage-proof (executed): hard-wired ``main()`` to ALWAYS print the
# envelope JSON regardless of ``--json`` — the ``"Generating briefing"``
# stderr assertion still passed (stderr is independent), but the
# ``"{" not in proc.stdout`` assertion fired because stdout now had
# the JSON dict instead of the empty preview; restored.
def test_brief_cli_subprocess_text_mode_unchanged(tmp_path: Path) -> None:
    """Text-mode output (no ``--json``) is regression-locked to the
    pre-PR-2.1 behaviour: degraded happy-path emits the operator trace
    on stderr and an empty content body on stdout (no JSON braces).
    """
    _seed_minimal_brief_workspace(tmp_path, "builder")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "brief",
            "builder",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env_without_llm_keys(),
        cwd=str(tmp_path),
        check=False,
    )

    assert proc.returncode == 0, (
        f"brief exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "Generating briefing for agent: builder" in proc.stderr, f"missing operator trace in stderr: {proc.stderr!r}"
    # Text mode must not emit JSON — if a future change accidentally
    # always renders the envelope, this assertion fires.
    assert "{" not in proc.stdout, f"text mode leaked JSON braces into stdout: {proc.stdout!r}"
    assert "}" not in proc.stdout, f"text mode leaked JSON braces into stdout: {proc.stdout!r}"
