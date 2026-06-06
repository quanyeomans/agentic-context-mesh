"""F30 outcome test — ``kairix research --json`` subprocess surface (PR 2.5).

PR 2.5 / #421 wired ``ResearchOutput.from_envelope`` so the warm MCP
path (PR 2.8) can route text-mode invocations through the envelope
shape. The pre-existing F30 outcome test
(``tests/integration/test_outcome_research_cli.py``) covers the
no-credential error envelope; this test rounds out the binary surface
by:

- Asserting the ``--json`` envelope shape over a subprocess invocation
  (degraded path, no LLM credential) — the contract the warm-MCP
  dispatcher in PR 2.8 will rely on.
- Asserting text-mode output (no ``--json``) emits the human-facing
  ``error:`` short-circuit and does NOT leak JSON braces — regression
  net against a future dispatcher change accidentally always rendering
  the envelope to stdout.

Subprocess env strips ``KAIRIX_LLM_API_KEY`` so the research path takes
the deterministic graceful-error branch regardless of the developer's
shell. Passing an explicit ``env=`` dict is the F2-clean way to control
a child process's environment (F2 targets pytest monkeypatch on
KAIRIX_* keys, not the subprocess ``env=`` parameter).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def _subprocess_env_without_llm_keys() -> dict[str, str]:
    """Return a subprocess env that strips LLM credential vars."""
    env = dict(os.environ)
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


# Sabotage-proof (executed): mutated ``main()`` in
# ``kairix/agents/research/cli.py`` to emit ``print("not json")``
# instead of ``print(json.dumps(...))`` under the ``--json`` branch —
# the ``json.loads(proc.stdout)`` assertion fired with JSONDecodeError;
# restored.
def test_research_cli_subprocess_json_envelope_carries_warm_mcp_shape() -> None:
    """Drive the real ``kairix research --json`` binary in the degraded
    error path; assert the envelope keys the warm-MCP dispatcher needs.

    F30 contract: outcome on stdout (parsed envelope), not just
    returncode. The exit-1 surface mirrors
    ``test_outcome_research_cli.py``; the divergent assertion here is
    that *every* key the ``from_envelope`` round-trip needs is present
    in the wire envelope.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "research",
            "what is the warm-MCP envelope shape?",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env_without_llm_keys(),
        check=False,
    )

    assert proc.returncode == 1, (
        f"expected exit 1 (no-LLM error path), got {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    # The from_envelope round-trip reads every key listed here; pin
    # the full set so a future MCP-side rename breaks the contract
    # loudly.
    for key in ("query", "synthesis", "retrieved_chunks", "gaps", "confidence", "turns", "error"):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert envelope["query"] == "what is the warm-MCP envelope shape?"
    assert envelope["error"], f"expected non-empty error on no-LLM path: {envelope}"


# Sabotage-proof (executed): hard-wired the research CLI ``main`` to
# always emit ``print(json.dumps(...))`` regardless of ``args.as_json``
# — the ``"{" not in proc.stdout`` assertion fired because text mode
# leaked JSON braces. Restored.
def test_research_cli_subprocess_text_mode_no_json_leak() -> None:
    """Text-mode output (no ``--json``) is regression-locked to the
    ``error: ...`` short-circuit format. Regression net against a
    future dispatcher change accidentally always rendering the
    envelope to stdout when the operator asked for plain text.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "research",
            "what is the warm-MCP envelope shape?",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env_without_llm_keys(),
        check=False,
    )

    assert proc.returncode == 1, (
        f"expected exit 1 (no-LLM error path), got {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    # The error branch in format_text short-circuits to ``error: <msg>``.
    assert proc.stdout.startswith("error:") or "error:" in proc.stdout, (
        f"expected text-mode 'error:' short-circuit in stdout: {proc.stdout!r}"
    )
    # If a future change accidentally always renders the envelope,
    # JSON braces appear; this assertion fires.
    assert "{" not in proc.stdout, f"text mode leaked JSON braces into stdout: {proc.stdout!r}"
    assert "}" not in proc.stdout, f"text mode leaked JSON braces into stdout: {proc.stdout!r}"
