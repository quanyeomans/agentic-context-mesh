"""F30 outcome test — ``kairix brief`` subprocess surface.

Plan B-parity post-mortem (2026-05-21) identified the gap: unit tests
asserted on internal call shapes; nothing exercised the subprocess
binary surface for each CLI subcommand. This is the briefing CLI's
half of the paydown — the existing unit tests in
``tests/briefing/test_cli.py`` continue to cover the function-call
seam (in-process ``deps=BriefDeps(...)`` injection); this test adds
the F30-required subprocess outcome assertion.

The ``--memory-root`` flag is the F2-clean subprocess seam — it was
already on the CLI before this paydown (the CLI threads the operator-
supplied root through ``set_agent_memory_root_override``). This commit
adds the outcome test and removes the baseline entry; no production
change.

Boundary chain exercised (degraded happy-path):

  subprocess([kairix, brief, <agent>, --memory-root <tmp>])
    → kairix/agents/briefing/cli.py:main
    → set_agent_memory_root_override(<tmp>)
    → kairix.use_cases.brief.run_brief
    → probe_health() → chat=offline (no KAIRIX_LLM_API_KEY in subprocess env)
    → returns BriefOutput(content="", path="", error="") + degraded health
    → CLI: no error branch, no path branch, format_output returns ""
    → exit 0, stderr has the "Generating briefing for agent: ..." line

Boundary chain exercised (invalid-agent error-path):

  subprocess([kairix, brief, <rogue-agent>])
    → run_brief returns BriefOutput(error="InvalidAgent: ...")
    → CLI prints "Error generating briefing: InvalidAgent..." to stderr
    → exit 1

The error path is independent of LLM availability — it gates on the
``_VALID_AGENTS`` set before any LLM call — so it's the stable F30
anchor.

Sabotage-proof (executed): mutated ``kairix/use_cases/brief.py``'s
``if normalised not in _VALID_AGENTS:`` guard to
``if False and normalised not in _VALID_AGENTS:`` — the invalid-agent
branch is unreachable, ``run_brief`` falls through to the LLM path,
which (with no LLM key in subprocess env) takes the degraded
``chat=offline`` branch and returns an empty-error envelope → CLI exits
0 with "Generating briefing for agent: rogue-agent" on stderr. The
invalid-agent test then fails on the ``returncode == 1`` assertion.
Restored.

Latency baseline (2024 M-series Mac): subprocess wall ~300-500ms
(interpreter + brief import graph + health probes). Test threshold:
10000ms (generous for the heavier brief module graph; bootstrap was
5000ms but brief pulls in more transitive imports).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed_minimal_memory_root(root: Path, agent: str) -> None:
    """Seed the minimum vault layout the brief use case + sources read.

    The brief pipeline's source fetchers tolerate missing files (they
    return empty content for the source), so a bare agent subdir is
    enough to drive the CLI's "Generating briefing for agent: ..."
    stderr line without crashing the sources step.
    """
    agent_dir = root / agent
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)


def _subprocess_env_without_llm_keys() -> dict[str, str]:
    """Return a subprocess env that strips LLM credential vars.

    The brief use case's health probe checks ``KAIRIX_LLM_API_KEY`` to
    decide ``chat=offline`` vs ``chat=ok``. We need a deterministic
    "chat offline" path in subprocess so the degraded happy-path test
    doesn't depend on the developer's shell having a real LLM key.

    Building this via ``{**os.environ, ...}`` minus the LLM keys is
    NOT ``monkeypatch.setenv("KAIRIX_*")`` — F2 targets pytest
    monkeypatch on KAIRIX_* keys. Passing an explicit env dict to
    ``subprocess.run`` is the F2-clean way to control a child
    process's environment.
    """
    env = dict(os.environ)
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


def test_brief_cli_subprocess_degraded_happy_path_outcome(tmp_path: Path) -> None:
    """Drive the real ``kairix brief`` binary against a tmp memory root.

    Without an LLM credential the use case takes the degraded path
    (``chat=offline``) and returns an empty-content envelope — exit 0,
    stderr carries the operator-facing "Generating briefing for agent:
    ..." line. Asserts on the stderr trace + clean exit, NOT on
    returncode alone, NOT on internal fake call-counts.
    """
    _seed_minimal_memory_root(tmp_path, "builder")

    t0 = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "brief",
            "builder",
            "--memory-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env_without_llm_keys(),
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"brief exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "Generating briefing for agent: builder" in proc.stderr, f"missing operator trace in stderr: {proc.stderr!r}"
    # Degraded path: no error message, no write-confirmation, no content body.
    assert "Error generating briefing" not in proc.stderr, f"unexpected error path in stderr: {proc.stderr!r}"

    assert elapsed_ms < 10000.0, f"brief subprocess took {elapsed_ms:.1f}ms (baseline ~300-500ms, threshold 10000ms)"


def test_brief_cli_subprocess_invalid_agent_exits_non_zero(tmp_path: Path) -> None:
    """Pointing at a non-existent agent name must surface a non-zero
    exit + the structured InvalidAgent error on stderr. Independent of
    LLM availability — the use case's agent-validation step runs before
    any LLM call, so this test is the F30 stable anchor.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "brief",
            "rogue-agent",
            "--memory-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env_without_llm_keys(),
    )

    assert proc.returncode == 1, f"expected exit 1 for invalid agent, got {proc.returncode}. stderr={proc.stderr!r}"
    assert "Error generating briefing" in proc.stderr, f"stderr missing error prefix: {proc.stderr!r}"
    assert "InvalidAgent" in proc.stderr, f"stderr missing InvalidAgent class name: {proc.stderr!r}"
