"""F30 outcome tests for ``kairix maintenance analyze`` (#376).

Two surfaces are exercised:

  * **In-process** — :func:`run_analyze_command` with an injected
    ``db_factory`` so the unit test stays sub-second and never touches
    the on-disk default path.
  * **Subprocess** — full ``python -m kairix.cli maintenance analyze
    --db-path <tmp>`` round-trip. F30 contract: assert on stdout
    content, not just returncode.

Sabotage-proof discipline: each test names the mutation that would
break it. Executed sabotages are reported in the dispatch report.
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance.cli import build_parser, run_analyze_command

pytestmark = pytest.mark.unit


_NOW = "2026-06-04T00:00:00Z"


def _seed_db_with_docs(db_path: Path, n: int = 5) -> None:
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    rows = [
        (
            "default",
            f"doc-{i:04d}.md",
            f"agent-alpha-{i:04d}",
            None,
            None,
            None,
            None,
            "public",
            _NOW,
            _NOW,
            1,
        )
        for i in range(n)
    ]
    db.executemany(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    db.close()


def test_run_analyze_command_emits_human_report(tmp_path: Path) -> None:
    """Text mode emits a multi-line operator report.

    Sabotage proof (executed): dropped the ``plan before:`` line from
    the text formatter — this assertion failed. Restored to make it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_db_with_docs(db_path, n=5)

    buf = io.StringIO()
    rc = run_analyze_command(db_path=db_path, out=buf, as_json=False)

    assert rc == 0
    out = buf.getvalue()
    assert "ANALYZE complete" in out, f"missing 'ANALYZE complete' in:\n{out}"
    assert "rows_analyzed=5" in out
    assert "elapsed_ms=" in out
    assert "plan before:" in out, f"missing EXPLAIN plan before-line in:\n{out}"
    assert "plan after:" in out, f"missing EXPLAIN plan after-line in:\n{out}"


def test_run_analyze_command_emits_json_envelope(tmp_path: Path) -> None:
    """``--json`` mode emits a parseable envelope with every contract field.

    Sabotage proof (executed): removed ``plan_after`` from the envelope
    dict and this assertion failed. Restored to make it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_db_with_docs(db_path, n=3)

    buf = io.StringIO()
    rc = run_analyze_command(db_path=db_path, out=buf, as_json=True)

    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["analyze_ran"] is True
    assert payload["rows_analyzed"] == 3
    assert payload["elapsed_ms"] >= 0.0
    assert payload["reason"]  # non-empty reason string
    assert "plan_before" in payload
    assert "plan_after" in payload
    assert "sample_query" in payload


def test_help_text_names_mcp_equivalent() -> None:
    """The subcommand --help names the MCP equivalent (operational-tests pattern 3).

    Sabotage proof (executed): dropped the "MCP equivalent: tool_maintenance_analyze"
    line from the analyze help text and this assertion failed.
    """
    parser = build_parser()
    help_text = parser.format_help()
    # The top-level parser advertises the analyze subcommand.
    assert "analyze" in help_text

    # Drill into the analyze subparser to assert the MCP-equivalent affordance.
    # argparse stashes subparsers as the ``choices`` attribute of the
    # subparser action; iterate the parser's actions to find it.
    subparser_action = next(action for action in parser._actions if hasattr(action, "choices") and action.choices)
    analyze_parser = subparser_action.choices["analyze"]
    analyze_help = analyze_parser.format_help()
    assert "tool_maintenance_analyze" in analyze_help, (
        f"analyze --help should name the MCP equivalent; got:\n{analyze_help}"
    )


def test_top_level_cli_dispatches_maintenance() -> None:
    """The top-level ``kairix`` CLI dispatch table knows about ``maintenance``."""
    from kairix.cli import COMMANDS

    assert "maintenance" in COMMANDS
    module_path, fn_name, accepts_args = COMMANDS["maintenance"]
    assert module_path == "kairix.core.maintenance.cli"
    assert fn_name == "main"
    assert accepts_args is True


def test_main_routes_analyze_subcommand(tmp_path: Path) -> None:
    """``main(["analyze", ...])`` invokes :func:`run_analyze_command` and
    returns its exit code. Sabotage: drop the dispatch line and the
    return value collapses to 2 (help).
    """
    from kairix.core.maintenance import cli as maintenance_cli

    db_path = tmp_path / "kairix.sqlite"
    _seed_db_with_docs(db_path, n=2)

    rc = maintenance_cli.main(["analyze", "--db-path", str(db_path), "--json"])
    assert rc == 0


def test_main_prints_help_when_no_subcommand(tmp_path: Path) -> None:
    """``main([])`` with no subcommand prints help and exits 2.

    Sabotage: change the return value to 0 and this assertion fires —
    the early-exit semantics are how operators discover the available
    subcommands.
    """
    from kairix.core.maintenance import cli as maintenance_cli

    rc = maintenance_cli.main([])
    assert rc == 2


def test_run_analyze_uses_deps_open_db(tmp_path: Path) -> None:
    """The ``AnalyzeCommandDeps.open_db`` field replaces the default open path.

    Sabotage: drop the ``deps.open_db`` call in :func:`run_analyze_command`
    and the injected callable is never invoked, so the test's call counter
    stays at zero.
    """
    from kairix.core.maintenance.cli import AnalyzeCommandDeps

    db_path = tmp_path / "kairix.sqlite"
    _seed_db_with_docs(db_path, n=2)

    factory_calls: list[Path | None] = []

    def opener(path: Path | None) -> sqlite3.Connection:
        factory_calls.append(path)
        # Always open the seeded path regardless of input — exercises the
        # seam without depending on env-var configured defaults.
        # F77-allow: out-of-process diagnostic test fixture
        return sqlite3.connect(str(db_path))

    deps = AnalyzeCommandDeps(open_db=opener)
    buf = io.StringIO()
    rc = run_analyze_command(db_path=None, out=buf, as_json=True, deps=deps)

    assert rc == 0
    assert len(factory_calls) == 1, f"deps.open_db should be called exactly once; got {factory_calls!r}"
    payload = json.loads(buf.getvalue())
    assert payload["rows_analyzed"] == 2


def test_run_analyze_handles_legacy_schema(tmp_path: Path) -> None:
    """Running against a DB with no schema applied still emits a valid envelope.

    Drives the missing-table sentinel branch through the public
    ``run_analyze_command`` surface so the assertion proves the
    end-to-end resilience contract: agents/operators get a structured
    response even on a half-initialised DB.

    Sabotage: drop the ``except sqlite3.OperationalError`` in
    ``_explain_plan`` and ``_count_documents`` and the command crashes
    instead of emitting "<schema missing>" / rows_analyzed=0.
    """
    from kairix.core.maintenance.cli import AnalyzeCommandDeps

    # Pass an opener that returns a bare in-memory connection — no schema.
    def opener(_path: Path | None) -> sqlite3.Connection:
        return sqlite3.connect(":memory:")

    deps = AnalyzeCommandDeps(open_db=opener)
    buf = io.StringIO()
    rc = run_analyze_command(db_path=None, out=buf, as_json=True, deps=deps)

    assert rc == 0
    payload = json.loads(buf.getvalue())
    # Legacy-schema branch: documents table doesn't exist, so the
    # rows count is 0 and the plan sample is the missing-schema sentinel.
    assert payload["rows_analyzed"] == 0
    assert payload["plan_before"] == "<schema missing>"


@pytest.mark.integration
def test_maintenance_analyze_subprocess_outcome(tmp_path: Path) -> None:
    """F30 outcome test — drive the real ``kairix maintenance analyze`` binary.

    Asserts on stdout content (not just returncode). The subprocess
    seam is ``--db-path``; no ``KAIRIX_*`` env vars in the subprocess
    environment (F2-clean).

    Sabotage proof (executed): dropped the EXPLAIN sample lines from
    the text formatter and the ``"plan before:"`` assertion failed.
    Restored to make it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_db_with_docs(db_path, n=7)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "maintenance",
            "analyze",
            "--db-path",
            str(db_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, (
        f"unexpected exit {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "ANALYZE complete" in proc.stdout, f"stdout missing 'ANALYZE complete':\n{proc.stdout}"
    assert "rows_analyzed=7" in proc.stdout
    assert "plan before:" in proc.stdout
    assert "plan after:" in proc.stdout


@pytest.mark.integration
def test_maintenance_analyze_subprocess_json_outcome(tmp_path: Path) -> None:
    """F30 JSON outcome test — same binary, --json mode emits valid envelope."""
    db_path = tmp_path / "kairix.sqlite"
    _seed_db_with_docs(db_path, n=4)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "maintenance",
            "analyze",
            "--db-path",
            str(db_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["analyze_ran"] is True
    assert envelope["rows_analyzed"] == 4
    assert "plan_before" in envelope
    assert "plan_after" in envelope
