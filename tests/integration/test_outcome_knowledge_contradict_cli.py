"""F30 outcome test — ``kairix contradict`` subprocess surface.

Pays down ``kairix/knowledge/contradict/cli.py`` from the F30 baseline.

The contradict CLI's full happy path needs an LLM backend AND a
populated search corpus — both heavy for a subprocess test. The CLI
already graceful-degrades when those are unavailable: the use case
captures the failure into ``ContradictOutput.error``, the formatter
renders an "error:" line (text) or an empty array (json), and the
process exits 1. That degraded path IS the operator-facing binary
contract this F30 test pins.

Boundary chain exercised:

  subprocess([kairix, contradict, check, "<claim>", --format json])
    → kairix/knowledge/contradict/cli.py:main
    → kairix.use_cases.contradict.run_contradict
    → check_fn (production: build_search_pipeline → LLM → detector)
    → on missing config: ContradictOutput.error populated
    → to_json_envelope renders [] on stdout
    → CLI exits 1

F2-clean: no ``KAIRIX_*`` env mutation in the subprocess invocation.

Sabotage-proof anchor: mutating ``to_json_envelope`` to return
``{"broken": True}`` instead of a list flips the json.loads result
type → the ``isinstance(..., list)`` assertion fails. Verified
locally.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_contradict_check_subprocess_json_envelope_outcome(tmp_path: Path) -> None:
    """Drive ``kairix contradict check ... --format json`` end-to-end.

    Asserts on the JSON envelope shape the operator (and the future
    contradiction-monitor wrapper) consumes — a list payload that the
    parser can iterate over. Even when the underlying LLM/search fails
    (no config in the subprocess env), the CLI must emit a syntactically
    valid JSON array on stdout.
    """
    del tmp_path  # CLI doesn't accept --document-root; signature parity with siblings

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "contradict",
            "check",
            "Outcome-test claim: the sky is green.",
            "--format",
            "json",
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
    )

    # CLI exits 1 when the use case error-populated (no LLM config in
    # subprocess env). The stdout envelope must still parse cleanly.
    envelope = json.loads(proc.stdout)
    assert isinstance(envelope, list), f"expected JSON list envelope, got {type(envelope).__name__}: {proc.stdout!r}"
    # Empty list when no contradictions found OR when underlying search
    # errored — both are valid envelope shapes per the contract.
    for hit in envelope:
        assert "doc_path" in hit, f"hit missing doc_path: {hit!r}"
        assert "score" in hit, f"hit missing score: {hit!r}"


def test_contradict_check_subprocess_text_renders_human_readable(tmp_path: Path) -> None:
    """``--format text`` must render an operator-facing line — either a
    "No contradictions found" header or a "1 contradiction(s) found"
    summary or an "error:" prefix when the underlying search errored.
    Closes the binary-surface error path the unit tests only cover in
    process."""
    del tmp_path

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "contradict",
            "check",
            "Another outcome-test claim.",
            "--format",
            "text",
            "--top-k",
            "1",
            "--top-claims",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    # One of three operator-facing shapes must appear on stdout. The
    # subprocess env lacks LLM config so "error:" is the expected branch
    # in CI; the other two land when a real config is present, so the
    # test stays robust across local/CI environments.
    stdout = proc.stdout
    has_no_contradictions = "No contradictions found" in stdout
    has_contradictions = "contradiction(s) found" in stdout
    has_error = stdout.startswith("error:")
    assert has_no_contradictions or has_contradictions or has_error, (
        f"contradict text output didn't match any expected shape: stdout={stdout!r} stderr={proc.stderr!r}"
    )

    # Exit code contract: 0 on success, 1 on use-case error.
    if has_error:
        assert proc.returncode == 1, f"text-format error must exit 1, got {proc.returncode}. stdout={stdout!r}"
    else:
        assert proc.returncode == 0, f"text-format success must exit 0, got {proc.returncode}. stdout={stdout!r}"
