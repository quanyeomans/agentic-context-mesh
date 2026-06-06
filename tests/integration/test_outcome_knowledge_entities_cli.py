"""F30 outcome test — ``kairix entity`` subprocess surface.

Pays down ``kairix/knowledge/entities/cli.py`` from the F30 baseline.

The entity CLI has six subcommands (``suggest``, ``validate``, ``seed``,
``get``, ``count``, ``audit``, ``purge``). Most require Neo4j to be
populated; ``suggest`` graceful-degrades to an empty list when Neo4j
is absent, which makes it the natural subprocess outcome path:

  subprocess([kairix, entity, suggest, "<text>", --format jsonl])
    → kairix/knowledge/entities/cli.py:main → cmd_suggest
    → kairix.use_cases.entity.run_entity_suggest
    → on missing Neo4j: empty EntitySuggestOutput, no error
    → format_suggest_output emits the "Total:" footer line
    → CLI exits 0

The error-path test pins the ``ERROR:`` prefix the CLI emits when the
use case populates ``error`` — exercised here by ``entity validate``
which hits Wikidata + Neo4j; when both are unavailable the binary
surface produces a structured stderr message and exits 1.

F2-clean: no ``KAIRIX_*`` env mutation in the subprocess invocation.

Sabotage-proof anchor: mutating ``format_suggest_output`` to drop the
"Total:" footer line makes the happy-path stdout assertion fail.
Verified locally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_entity_suggest_subprocess_table_outcome(tmp_path: Path) -> None:
    """``kairix entity suggest`` over a small text — operator sees the
    "Total:" footer line even when no entities are found.

    This pins the binary surface: argparse → cmd_suggest →
    format_suggest_output → stdout. Sabotaging the formatter breaks
    this assertion.
    """
    del tmp_path  # CLI doesn't accept --document-root for this subcommand

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "entity",
            "suggest",
            "Alpha Corp builds widgets. Beta Org sells them.",
            "--format",
            "table",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"entity suggest exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    # The format_suggest_output table view always prints a "Total: N
    # entities found" footer; degraded mode (no Neo4j) prints "Total: 0".
    assert "Total:" in proc.stdout, f"footer missing from stdout: {proc.stdout!r}"
    assert "entities found" in proc.stdout, f"footer keyword missing: {proc.stdout!r}"


def test_entity_count_subprocess_returns_nonzero_without_neo4j(tmp_path: Path) -> None:
    """Without a Neo4j driver installed, ``kairix entity count`` must
    surface the connection failure on stderr and exit non-zero.

    The CLI prints an ERROR-prefixed message to stderr (not stdout) so
    healthcheck wrappers can route it. Closes the binary-surface error
    path the unit tests cover only in-process.
    """
    del tmp_path

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "entity",
            "count",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, (
        f"entity count expected exit 1 without Neo4j, got {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "ERROR" in proc.stderr, f"expected ERROR prefix on stderr, got: {proc.stderr!r}"
    assert "Neo4j" in proc.stderr, f"expected Neo4j keyword in error, got: {proc.stderr!r}"


def test_entity_enrich_subprocess_requires_target_flag(tmp_path: Path) -> None:
    """``kairix entity enrich`` without --name or --all-missing must fail
    at argparse (the target flags are mutually-exclusive + required).

    Pins the binary surface contract for #415: operators can't
    accidentally run enrichment with no scope. Argparse exits 2 on
    missing-required and writes the usage line to stderr.

    Sabotage-proof: dropping ``required=True`` from the
    mutually_exclusive_group → argparse exits 0 and the test fails on
    the exit-code assertion.
    """
    del tmp_path

    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "entity", "enrich"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 2, (
        f"entity enrich without target should exit 2, got {proc.returncode}. stderr={proc.stderr!r}"
    )
    # argparse emits "one of the arguments --name --all-missing is required" on stderr.
    assert "--name" in proc.stderr and "--all-missing" in proc.stderr, (
        f"expected mutually-exclusive arg names in stderr, got: {proc.stderr!r}"
    )


def test_entity_enrich_subprocess_degrades_gracefully_without_neo4j(tmp_path: Path) -> None:
    """``kairix entity enrich --name X`` without a live Neo4j client emits
    the "Skipped: entity not found" message and exits 0 — graceful
    degradation matches the validate/suggest pattern (no Neo4j = empty
    lookup, not crash).

    Pins the binary surface for #415: operators in dev environments
    (no Neo4j password) get a clear no-op message rather than a stack
    trace. Sabotage-proof: changing format_enrich_text to drop the
    "Skipped:" branch makes this assertion fail.
    """
    del tmp_path

    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "entity", "enrich", "--name", "Acme Corp"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"entity enrich graceful degradation expected exit 0, got {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "Entity: Acme Corp" in proc.stdout, f"expected entity header in stdout, got: {proc.stdout!r}"
    assert "Skipped" in proc.stdout, f"expected Skipped marker in stdout, got: {proc.stdout!r}"
