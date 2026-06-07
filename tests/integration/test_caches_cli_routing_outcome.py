"""F30 outcome test — ``kairix caches`` subprocess behaviour (PR 3.1 / #422).

Drives the real ``python -m kairix.cli caches`` binary so the F30
contract is satisfied: outcome assertions on stdout / stderr / envelope
content, not on returncode alone.

Two subprocess scenarios:

1. ``kairix caches --json`` with no MCP endpoint configured to a
   responsive port — the dispatcher returns None (responsiveness probe
   fails), the in-process collectors run, and the banner appears on
   stderr while stdout stays valid JSON.

2. ``kairix caches`` (text mode) same cold-MCP setup — banner on
   stderr, in-process text table on stdout.

The CI environment never has a warm MCP server, so this outcome test
exercises the cold-path branch end-to-end. The warm-path branch is
exercised in unit + BDD tests via the ``CachesDeps`` injection seam.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


_BANNER_FRAGMENT = "MCP server not responsive"


def _kairix_pythonpath() -> str:
    """Return the directory containing the kairix package to use in the subprocess.

    The venv's editable install MAPPING hard-codes the primary checkout
    path and ignores worktrees, so a subprocess invocation always loads
    the primary checkout's kairix module — NOT the worktree's. This
    helper derives the repo root from THIS test file's path, which sits
    inside the worktree, so the subprocess loads the worktree's code
    (where the new behaviour under test actually lives).
    """
    # tests/integration/test_caches_cli_routing_outcome.py
    # → tests/integration/ → tests/ → <repo-root>
    return str(Path(__file__).resolve().parent.parent.parent)


def _run_caches(argv: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run ``python -m kairix.cli caches <argv>`` and capture both streams.

    The subprocess inherits no ``KAIRIX_*`` env vars (F2-clean by
    construction); the document-root is the tmp_path sandbox.
    PYTHONPATH is set so the subprocess loads the same kairix module
    pytest just imported (worktree-safe per the comment in
    :func:`_kairix_pythonpath`).
    """
    env = {**os.environ, "PYTHONPATH": _kairix_pythonpath()}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "caches",
            *argv,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=env,
        check=False,
    )


# Sabotage-proof (executed): removed the stderr banner write from
# ``caches_cli.main``; the subprocess test failed because stderr did
# not carry the banner fragment. Restored.
def test_caches_json_cold_mcp_emits_envelope_on_stdout_and_banner_on_stderr(
    tmp_path: Path,
) -> None:
    """``kairix caches --json`` against an empty environment emits a
    valid JSON envelope on stdout AND the cold-MCP banner on stderr."""
    proc = _run_caches(["--json"], tmp_path)

    assert proc.returncode == 0, (
        f"unexpected exit {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    # stdout is parseable JSON envelope (banner did not contaminate it)
    envelope = json.loads(proc.stdout)
    assert "caches" in envelope
    assert isinstance(envelope["caches"], list)
    # stderr carries the banner — the operator sees the fall-through warning
    assert _BANNER_FRAGMENT in proc.stderr, f"banner missing from stderr: {proc.stderr!r}"


# Sabotage-proof (executed): made the in-process fall-through skip the
# collectors when MCP routing was attempted; the text-mode test failed
# because stdout was empty. Restored.
def test_caches_text_cold_mcp_emits_table_and_banner(tmp_path: Path) -> None:
    """``kairix caches`` (text mode) emits the report on stdout AND
    the banner on stderr above the table."""
    proc = _run_caches([], tmp_path)

    assert proc.returncode == 0, (
        f"unexpected exit {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "kairix caches" in proc.stdout
    # Every W-B cache name lands in stdout.
    for name in (
        "query_result_cache",
        "prep_summary_cache",
        "brief_output_cache",
        "brief_source_cache",
        "health_probe_cache",
    ):
        assert name in proc.stdout, f"missing cache name {name!r} in text report"
    # Banner lives on stderr.
    assert _BANNER_FRAGMENT in proc.stderr, f"banner missing from stderr: {proc.stderr!r}"
