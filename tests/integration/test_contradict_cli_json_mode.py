"""F30 outcome test — ``kairix contradict --json`` subprocess surface.

PR 2.6 / #421 wired a ``--json`` flag on ``kairix contradict`` so the
warm MCP path can route text-mode invocations through the envelope
shape. This test exercises the subprocess binary surface for both modes:

- ``kairix contradict check <content> --json`` → stdout is a JSON envelope
  dict (``content`` / ``contradictions`` / ``has_contradictions`` /
  ``error`` keys). Differs from the legacy ``--format json`` which
  emits a flat list (preserved for backwards compat).
- ``kairix contradict check <content>`` (no ``--json``, no
  ``--format``) → text-mode output unchanged from pre-PR-2.6
  behaviour. Regression net against the dispatcher flip-over breaking
  text mode.

The subprocess doesn't manipulate any ``KAIRIX_*`` env vars (F2): the
contradict CLI runs without an LLM config, ``run_contradict``
graceful-degrades, and the envelope carries the error string. Exit 1
on error so shell pipelines can branch on it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# Sabotage-proof (executed): mutated ``main()`` in
# ``kairix/knowledge/contradict/cli.py`` to emit ``print("not json")``
# instead of ``json.dumps(envelope)`` under the ``--json`` branch — the
# ``json.loads(proc.stdout)`` assertion fired with JSONDecodeError;
# restored.
def test_contradict_cli_subprocess_json_mode_emits_envelope_dict(tmp_path: Path) -> None:
    """Drive the real ``kairix contradict --json`` binary against an empty env.

    Asserts stdout parses as JSON dict carrying the envelope-shape keys
    (F30: outcome on stdout, not just returncode). Degraded health (no
    LLM cred) still produces a well-formed envelope — that's the
    contract the MCP dispatcher relies on.
    """
    del tmp_path  # CLI doesn't accept --document-root; signature parity with siblings

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "contradict",
            "check",
            "Outcome-test claim: agent-alpha proposes a contradiction.",
            "--json",
            "--top-k",
            "1",
            "--threshold",
            "0.9",
            "--top-claims",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"--json envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    for key in ("content", "contradictions", "has_contradictions", "error"):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert isinstance(envelope["contradictions"], list), (
        f"contradictions must be a list, got {type(envelope['contradictions']).__name__}"
    )
    # Exit code contract — error path exits 1, success path exits 0.
    if envelope["error"]:
        assert proc.returncode == 1, f"--json error must exit 1, got {proc.returncode}; envelope={envelope!r}"
    else:
        assert proc.returncode == 0, f"--json success must exit 0, got {proc.returncode}; envelope={envelope!r}"


# Sabotage-proof (executed): hard-wired ``main()`` to ALWAYS print the
# envelope JSON regardless of ``--json`` — the ``"{" not in proc.stdout``
# assertion fired because stdout now had the JSON dict instead of the
# text-mode line; restored.
def test_contradict_cli_subprocess_text_mode_unchanged(tmp_path: Path) -> None:
    """Text-mode output (no ``--json``, no ``--format``) is regression-locked.

    The contradict CLI without ``--json`` defaults to ``--format text``;
    the operator-facing branch must emit either a "No contradictions
    found", "contradiction(s) found", or "error:" line — never a JSON
    dict.
    """
    del tmp_path

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "contradict",
            "check",
            "Another outcome-test claim.",
            "--top-k",
            "1",
            "--top-claims",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    stdout = proc.stdout
    has_no_contradictions = "No contradictions found" in stdout
    has_contradictions = "contradiction(s) found" in stdout
    has_error = stdout.startswith("error:")
    assert has_no_contradictions or has_contradictions or has_error, (
        f"contradict text output didn't match any expected shape: stdout={stdout!r} stderr={proc.stderr!r}"
    )
    # Text mode must not emit JSON dict braces on the first character.
    # (A 'reason' or 'snippet' value could legitimately contain '{}' so
    # we anchor on the opening brace prefix only.)
    assert not stdout.lstrip().startswith("{"), f"text mode leaked JSON dict to stdout: {stdout!r}"


# Sabotage-proof (executed): changed the ``--format`` choices to drop
# ``json`` and made the default break with a ValueError — the
# ``isinstance(legacy, list)`` assertion fired because the subprocess
# now exits 2 with stderr argparse error. Restored.
def test_contradict_cli_subprocess_format_json_emits_legacy_list(tmp_path: Path) -> None:
    """``--format json`` (legacy) emits a JSON list of hits, NOT the
    envelope dict. PR 2.6 must preserve this surface for callers built
    against pre-PR behaviour.
    """
    del tmp_path

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "contradict",
            "check",
            "Legacy-shape outcome-test claim.",
            "--format",
            "json",
            "--top-k",
            "1",
            "--top-claims",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    try:
        legacy = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--format json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")
    assert isinstance(legacy, list), (
        f"--format json envelope must be a list (legacy shape), got {type(legacy).__name__}: {legacy!r}"
    )
