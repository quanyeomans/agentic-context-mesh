"""F30 outcome test — ``kairix prep --json`` subprocess surface.

PR 2.4 / #421 plumbed the warm-MCP envelope seam onto ``kairix prep``.
The CLI's pre-existing ``--json`` flag remains the operator's structured
output channel; PR 2.4 added ``PrepOutput.from_envelope`` so the warm
text path can route envelope -> dataclass -> ``format_text`` without
drift. This test exercises the subprocess binary surface for both modes:

- ``kairix prep '<query>' --json`` -> stdout is a JSON envelope dict
  (``query`` / ``tier`` / ``summary`` / ``sources`` / ``error`` keys),
  parseable whether the run hit a configured provider or the degraded
  no-provider path. Mirrors ``test_outcome_search_cli.py``.
- ``kairix prep '<query>'`` (no ``--json``) -> text-mode output unchanged
  from pre-PR-2.4 behaviour. Regression net against the envelope seam
  accidentally flipping text mode to JSON output.

F2-clean: no ``KAIRIX_*`` env vars are set on the subprocess; both modes
rely on the envelope-shape contract that ``run_prep`` upholds regardless
of provider readiness.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


# Sabotage-proof (executed): mutated ``kairix/agents/prep/cli.py``
# ``main()`` to emit ``print("not json")`` instead of
# ``print(json.dumps(prep_output_to_envelope(out), indent=2))`` under
# the ``--json`` branch — the ``json.loads(proc.stdout)`` assertion
# fired with JSONDecodeError; restored.
def test_prep_cli_subprocess_json_mode_emits_envelope_dict() -> None:
    """Drive the real ``kairix prep --json`` binary; assert envelope shape.

    Whether the host has a configured provider or not, ``run_prep``
    returns a ``PrepOutput`` that projects to a structured envelope —
    that's the F30 contract this test pins. The envelope's ``error``
    field captures the degraded path; the shape keys are present on
    both branches.
    """
    query = "what is kairix prep"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "prep",
            query,
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    # The envelope is the load-bearing assertion. Even on the degraded
    # no-provider path the CLI still emits a parseable envelope.
    assert proc.stdout, f"empty stdout — subprocess crashed before envelope render. stderr={proc.stderr!r}"

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    for key in ("query", "tier", "summary", "tokens", "sources", "error"):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert envelope["query"] == query, f"query echo missing/mismatched: {envelope.get('query')!r}"
    # Tier defaults to ``l0`` when not passed.
    assert envelope["tier"] == "l0"

    # Exit code mirrors the search CLI semantics: 0 on success-or-degraded
    # graceful path; 1 when the use case populates ``error``. Both branches
    # produce a parseable envelope — that's the F30 contract.
    if envelope.get("error"):
        assert proc.returncode == 1, (
            f"expected exit 1 when envelope has error; got {proc.returncode}; stderr={proc.stderr!r}"
        )
    else:
        assert proc.returncode == 0, (
            f"expected exit 0 when envelope has no error; got {proc.returncode}; stderr={proc.stderr!r}"
        )


# Sabotage-proof (executed): hard-wired ``main()`` to ALWAYS print the
# envelope JSON regardless of ``--json`` — the ``"{" not in proc.stdout``
# assertion fired because text mode now leaked JSON braces; restored.
def test_prep_cli_subprocess_text_mode_unchanged() -> None:
    """Text-mode output (no ``--json``) is regression-locked to the
    pre-PR-2.4 behaviour: stdout carries either the human-readable
    ``Query:`` / ``Tier:`` / summary lines (provider-configured path)
    or the ``error:`` short-circuit line (degraded path). Neither
    branch leaks JSON braces into stdout.
    """
    query = "what is kairix prep text mode"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "prep",
            query,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.stdout, f"empty stdout — subprocess crashed before text render. stderr={proc.stderr!r}"

    # Text mode must not emit a JSON object — guards against an accidental
    # flip-over of the format branch.
    stripped = proc.stdout.strip()
    assert not (stripped.startswith("{") and stripped.endswith("}")), (
        f"text mode leaked JSON object into stdout: {proc.stdout!r}"
    )

    # Either the success path (Query: prefix) or the error short-circuit
    # (error: prefix) — both are the legitimate format_text outputs.
    assert "Query:" in proc.stdout or "error:" in proc.stdout, (
        f"text mode stdout matched no known prefix: {proc.stdout!r}"
    )
