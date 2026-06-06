"""F30 outcome test — ``kairix bootstrap --json`` subprocess surface.

PR 2.3 / #421 brings ``kairix bootstrap`` into the warm-MCP routing
contract (PR 2.8) by pinning that ``--json`` emits the same envelope
shape ``tool_bootstrap`` returns over MCP. This test exercises the
subprocess binary surface for both modes:

- ``kairix bootstrap --json <agent>`` → stdout is a JSON envelope dict
  (``agent`` / ``role`` / ``board`` / ``recent_memory`` / ``health``
  / ``next_action`` / ``error`` keys), exit 0 on the degraded happy
  path.
- ``kairix bootstrap <agent>`` (no ``--json``) → markdown output
  unchanged from pre-PR-2.3 behaviour. Regression net against the
  dispatcher flip-over breaking text mode.

The ``--document-root`` flag is the F2-clean subprocess seam: the
operator-supplied root is threaded through ``BootstrapDeps`` without
any ``KAIRIX_*`` env-var manipulation.

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
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed_minimal_vault(root: Path, agent: str) -> None:
    """Seed the minimum vault layout the bootstrap use case reads."""
    agent_dir = root / "04-Agent-Knowledge" / agent
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)
    (agent_dir / "Board.md").write_text("priorities: ship", encoding="utf-8")
    (agent_dir / "Goals.md").write_text("- ship the composer", encoding="utf-8")
    (agent_dir / "profile.md").write_text("# Builder\n", encoding="utf-8")


def _subprocess_env_without_llm_keys() -> dict[str, str]:
    """Return a subprocess env that strips LLM credential vars."""
    env = dict(os.environ)
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


# Sabotage-proof (executed): mutated ``main()`` in
# ``kairix/bootstrap_cli.py`` to write ``out_sink.write("not json\n")``
# under the ``--json`` branch — the ``json.loads(proc.stdout)`` assertion
# fired with JSONDecodeError; restored.
def test_bootstrap_cli_subprocess_json_mode_emits_envelope_dict(tmp_path: Path) -> None:
    """Drive the real ``kairix bootstrap --json`` binary against a tmp
    document root.

    Asserts stdout parses as JSON and carries the envelope-shape keys
    (F30: outcome on stdout, not just returncode). Degraded health
    (no LLM cred) still produces a well-formed envelope — that's the
    contract the MCP dispatcher will rely on once PR 2.8 lands.
    """
    _seed_minimal_vault(tmp_path, "agent-alpha")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "bootstrap",
            "agent-alpha",
            "--json",
            "--document-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env_without_llm_keys(),
        check=False,
    )

    assert proc.returncode == 0, (
        f"bootstrap --json exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    for key in ("agent", "role", "board", "recent_memory", "active_goals", "health", "next_action", "error"):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert envelope["agent"] == "agent-alpha"
    assert "vector_search" in envelope["health"]


# Sabotage-proof (executed): hard-wired ``main()`` to ALWAYS write the
# envelope JSON regardless of ``--json`` — the markdown-mode assertion
# on ``"# Bootstrap envelope: agent-alpha" in proc.stdout`` still passed
# (the header happens to embed in the JSON value as a string), but the
# ``proc.stdout.lstrip().startswith("{")`` assertion fired because the
# stdout now started with ``{`` instead of ``#``; restored.
def test_bootstrap_cli_subprocess_markdown_mode_unchanged(tmp_path: Path) -> None:
    """Markdown mode output (no ``--json``) is regression-locked to the
    pre-PR-2.3 behaviour: stdout starts with the markdown header and
    contains the ``## Health`` / ``## Board`` sections (no JSON braces
    at the top of the document)."""
    _seed_minimal_vault(tmp_path, "agent-alpha")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "bootstrap",
            "agent-alpha",
            "--document-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env_without_llm_keys(),
        check=False,
    )

    assert proc.returncode == 0, (
        f"bootstrap exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    # Markdown mode emits the header first — proves the dispatcher kept
    # the existing rendering branch when ``--json`` isn't passed.
    assert proc.stdout.lstrip().startswith("# Bootstrap envelope: agent-alpha"), (
        f"markdown mode missing header at top of stdout: {proc.stdout[:200]!r}"
    )
    assert "## Health" in proc.stdout, f"markdown missing Health section: {proc.stdout[:400]!r}"
    assert "## Board" in proc.stdout, f"markdown missing Board section: {proc.stdout[:400]!r}"
    # The first non-blank stdout char must NOT be a JSON brace — guards
    # against a regression where the dispatcher ignored the ``--json``
    # flag and always emitted JSON.
    assert not proc.stdout.lstrip().startswith("{"), (
        f"markdown mode leaked JSON braces at top of stdout: {proc.stdout[:200]!r}"
    )
