"""F30 outcome test — ``kairix usage-guide`` subprocess surface.

Plan B-parity post-mortem (2026-05-21) identified the gap: unit tests
asserted on internal call shapes; nothing exercised the subprocess
binary surface for each CLI subcommand. This is the usage-guide CLI's
half of the paydown — the existing unit tests in
``tests/agents/usage_guide/test_cli.py`` continue to cover the
function-call seam (in-process ``deps=UsageGuideDeps(...)`` injection);
this test adds the F30-required subprocess outcome assertion.

The ``--guide-path`` flag is the F2-clean subprocess seam — it was
already on the CLI before this paydown (the use case accepts an
explicit ``Path`` and bypasses the production resolver), so the only
change in this commit is adding the outcome test and removing the
baseline entry.

Boundary chain exercised:

  subprocess([kairix, usage-guide, [topic], --guide-path <tmp.md>, --json])
    → kairix/agents/usage_guide/cli.py:main
    → kairix.use_cases.usage_guide.run_usage_guide
    → resolve_guide_fn (defaults — but guide_path arg short-circuits it)
    → read_text(<tmp.md>) → extract_topic_sections (when topic given)
    → usage_guide_output_to_envelope
    → JSON to stdout

Sabotage-proof: confirmed by mutating
``out = run_usage_guide(args.topic, guide_path=args.guide_path, deps=deps)``
to ``out = run_usage_guide(args.topic, guide_path=None, deps=deps)``
inside the CLI's ``main`` — the use case then resolves the production
guide path instead of the tmp file, the topic-section extractor finds
no match for the unique sentinel ``F30-OUTCOME-SENTINEL`` and the
content assertion fails. Restored.

Latency baseline (2024 M-series Mac): subprocess wall ~150-300ms (cold
Python interpreter + import graph dominate). Test threshold: 5000ms
(~15x headroom for CI variance + slower hardware).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


_GUIDE_FIXTURE = """# Usage guide fixture

## Search
Use ``kairix search`` to query the knowledge store.
F30-OUTCOME-SENTINEL search-section

## Budget
The agent has a budget per turn. Avoid wasting tokens.
F30-OUTCOME-SENTINEL budget-section
"""


def _seed_guide(tmp_path: Path) -> Path:
    """Write the minimal usage-guide markdown fixture and return its path."""
    guide = tmp_path / "agent-usage-guide.md"
    guide.write_text(_GUIDE_FIXTURE, encoding="utf-8")
    return guide


def test_usage_guide_cli_subprocess_topic_envelope_outcome(tmp_path: Path) -> None:
    """Drive the real ``kairix usage-guide`` binary surface against a tmp guide.

    Asserts on the JSON envelope content (topic + content + error fields)
    the agent harness consumes — NOT on returncode alone, NOT on
    internal fake call-counts. The F30 contract: subprocess + stdout
    envelope assertion.
    """
    guide = _seed_guide(tmp_path)

    t0 = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "usage-guide",
            "budget",
            "--guide-path",
            str(guide),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"usage-guide exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    envelope = json.loads(proc.stdout)
    assert envelope["topic"] == "budget", f"envelope: {envelope}"
    assert "budget-section" in envelope["content"], (
        f"expected the budget section in content; got: {envelope.get('content', '')[:200]!r}"
    )
    assert "search-section" not in envelope["content"], (
        f"topic filter leaked the search section: {envelope.get('content', '')[:200]!r}"
    )
    assert envelope["error"] == "", f"unexpected error: {envelope.get('error')!r}"

    assert elapsed_ms < 5000.0, f"usage-guide subprocess took {elapsed_ms:.1f}ms (baseline ~200ms, threshold 5000ms)"


def test_usage_guide_cli_subprocess_missing_guide_emits_error(tmp_path: Path) -> None:
    """Pointing ``--guide-path`` at a non-existent file must surface a
    non-zero exit + a parseable error message on stdout (the use case
    returns ``UsageGuideNotFound`` and the text formatter prefixes it
    with ``error:``). Closes the binary-surface error path that the unit
    tests cover only in-process.
    """
    bogus = tmp_path / "does-not-exist.md"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "usage-guide",
            "--guide-path",
            str(bogus),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, f"expected exit 1 for missing guide, got {proc.returncode}. stderr={proc.stderr!r}"
    assert "error:" in proc.stdout, f"stdout missing 'error:' prefix: {proc.stdout!r}"
    assert "UsageGuideNotFound" in proc.stdout, f"stdout missing UsageGuideNotFound class name: {proc.stdout!r}"
