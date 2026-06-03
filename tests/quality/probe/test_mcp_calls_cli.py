"""Unit + outcome tests for ``kairix probe mcp-calls`` (#398 W-D).

Drives :func:`kairix.quality.probe.mcp_calls_cli.main` against a
tmp-path SQLite seeded with rows that mirror the production INSERT
shape. Plus an F30 subprocess outcome test that boots the whole
``python -m kairix.cli probe mcp-calls`` binary.

F1-clean (no @patch), F2-clean (no env var), F5-clean (no private
imports). Each test documents its sabotage proof in the docstring.
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.quality.probe.mcp_calls_cli import (
    McpCallsDeps,
    ToolStats,
    main,
)

pytestmark = pytest.mark.unit


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    create_schema(conn)
    conn.close()
    return db_path


def _seed(db_path: Path, rows: list[tuple[str, str, str | None, int, int, str | None]]) -> None:
    """Seed mcp_call_log with the (timestamp, tool, agent, latency_ms, success, error_class) shape.

    Idempotently creates the table first so the helper works against
    both the legacy main-DB shape (where the canonical schema creates
    the table) and the new dedicated mcp_observability.sqlite shape
    (where the wrapper creates the table on first write).
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mcp_call_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,"
        " tool TEXT NOT NULL, agent TEXT, latency_ms INTEGER NOT NULL,"
        " success INTEGER NOT NULL, error_class TEXT, payload_hash TEXT"
        ")"
    )
    try:
        for ts, tool, agent, latency_ms, success, error_class in rows:
            conn.execute(
                "INSERT INTO mcp_call_log "
                "(timestamp, tool, agent, latency_ms, success, error_class, payload_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, tool, agent, latency_ms, success, error_class, "hash000000000000"),
            )
        conn.commit()
    finally:
        conn.close()


def _run_capture(argv: list[str], db_path: Path) -> tuple[int, str, str]:
    """Invoke main with injected deps and capture (rc, stdout, stderr)."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    deps = McpCallsDeps(db_path_fn=lambda: db_path)
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = main(argv, deps=deps)
    return rc, out_buf.getvalue(), err_buf.getvalue()


def test_empty_table_emits_no_calls_message(tmp_path: Path) -> None:
    """Empty mcp_call_log → operator-friendly message + exit 0.

    Sabotage proof: changed the empty-stats branch to return "" (empty
    string) instead of the message — this test fails (the assertion
    looks for the phrase). Restored.
    """
    db_path = _make_db(tmp_path)
    rc, out, _err = _run_capture([], db_path)
    assert rc == 0
    assert "no calls recorded" in out


def test_text_mode_renders_per_tool_stats(tmp_path: Path) -> None:
    """Seeded rows render as per-tool rows with latency + ok% columns.

    Sabotage proof: changed the per-tool sort key from
    ``s.count`` to ``-s.count`` (reverses order) — the assertion that
    'search' appears in the output still passes (descending vs
    ascending doesn't matter for presence), but the SECOND scenario
    in the BDD feature checks 'both names appear'. Better sabotage:
    skip the row append in _build_tool_stats — this test fails (no
    rows). Restored.
    """
    db_path = _make_db(tmp_path)
    _seed(
        db_path,
        [
            ("2026-06-03T10:00:00Z", "search", "shape", 100, 1, None),
            ("2026-06-03T10:00:01Z", "search", "shape", 200, 1, None),
            ("2026-06-03T10:00:02Z", "search", "shape", 9000, 0, "TimeoutError"),
        ],
    )
    rc, out, _err = _run_capture([], db_path)
    assert rc == 0
    assert "search" in out
    assert "TimeoutError" in out  # appears in top_errors column
    # Check that one of count/p50/p95 numbers shows up — the row was rendered.
    assert "3" in out  # count=3


def test_json_mode_emits_envelope(tmp_path: Path) -> None:
    """``--json`` emits a parseable envelope with the expected fields.

    Sabotage proof: changed ``json.dumps`` to ``str(payload)`` — this
    test fails (json.loads raises). Restored.
    """
    db_path = _make_db(tmp_path)
    _seed(
        db_path,
        [
            ("2026-06-03T10:00:00Z", "brief", "shape", 1500, 1, None),
            ("2026-06-03T10:00:01Z", "brief", "shape", 1800, 0, "RuntimeError"),
        ],
    )
    rc, out, _err = _run_capture(["--json"], db_path)
    assert rc == 0
    payload = json.loads(out)
    assert "tools" in payload
    tools = payload["tools"]
    assert len(tools) == 1
    entry = tools[0]
    assert entry["tool"] == "brief"
    assert entry["count"] == 2
    assert entry["p50_ms"] >= 1500
    assert any(err["class"] == "RuntimeError" for err in entry["top_errors"])


def test_tool_filter_narrows_results(tmp_path: Path) -> None:
    """``--tool brief`` excludes other tools from the report.

    Sabotage proof: removed the ``tool = ?`` filter in _query_rows
    — the report would include 'search' too; the test fails on the
    'search' absence assertion. Restored.
    """
    db_path = _make_db(tmp_path)
    _seed(
        db_path,
        [
            ("2026-06-03T10:00:00Z", "search", "shape", 100, 1, None),
            ("2026-06-03T10:00:01Z", "brief", "shape", 1500, 1, None),
        ],
    )
    rc, out, _err = _run_capture(["--json", "--tool", "brief"], db_path)
    assert rc == 0
    payload = json.loads(out)
    tools = {t["tool"] for t in payload["tools"]}
    assert tools == {"brief"}, f"expected only 'brief'; got {tools!r}"


def test_malformed_since_returns_exit_2(tmp_path: Path) -> None:
    """``--since potato`` emits F21-shaped error + exit 2.

    Sabotage proof: dropped the ValueError raise in ``_parse_since`` —
    the bad token returns an empty timestamp filter, the CLI exits 0,
    test fails on rc == 2. Restored.
    """
    db_path = _make_db(tmp_path)
    rc, _out, err = _run_capture(["--since", "potato"], db_path)
    assert rc == 2
    assert "fix:" in err
    assert "s/m/h/d" in err or "duration" in err.lower()


def test_since_accepts_h_unit(tmp_path: Path) -> None:
    """``--since 1h`` parses and applies a 1-hour lower bound.

    Drives the duration-parser through the public CLI surface.

    Sabotage proof: removed ``"h"`` from _DURATION_UNITS — the test
    fails when ``_parse_since`` raises ValueError. Restored.
    """
    db_path = _make_db(tmp_path)
    rc, _out, _err = _run_capture(["--since", "1h"], db_path)
    assert rc == 0


def test_missing_table_emits_no_calls_message(tmp_path: Path) -> None:
    """A DB without mcp_call_log emits the friendly no-calls message + exit 0.

    Post 2026-06-04 fix: the observability table now lives in its own
    SQLite file (``mcp_observability.sqlite``) and is auto-created on
    first INSERT by the per-call wrapper. A reader landing on a file
    without the table means "no MCP calls have run yet" — the honest
    answer is "no calls" not "missing migration".

    Sabotage proof: removed the ``"no such table" in str(exc).lower()``
    branch in ``main()`` — this test fails when the operational-error
    actionable-message path fires for a missing-table read instead of
    short-circuiting to the empty-rows happy path. Restored.
    """
    db_path = tmp_path / "no-mcp-call-log.sqlite"
    conn = sqlite3.connect(str(db_path))
    # Don't create the schema — mcp_call_log won't exist.
    conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    rc, out, _err = _run_capture([], db_path)
    assert rc == 0
    assert "no calls recorded" in out.lower()


def test_db_path_fn_raises_returns_exit_2(tmp_path: Path) -> None:
    """db_path_fn raising surfaces as an actionable error + exit 2.

    Sabotage proof: removed the try/except around resolve_db_path() —
    the test fails when the RuntimeError propagates. Restored.
    """

    def _raises() -> Path:
        raise RuntimeError("paths broken")

    deps = McpCallsDeps(db_path_fn=_raises)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = main([], deps=deps)
    assert rc == 2
    assert "could not resolve DB path" in err_buf.getvalue()


def test_tool_stats_dataclass_is_frozen() -> None:
    """ToolStats is frozen so the projection is immutable (F42-clean).

    Sabotage proof: removed ``frozen=True`` from @dataclass — this
    test fails (mutation succeeds). Restored.
    """
    stats = ToolStats(
        tool="search",
        count=1,
        p50_ms=100,
        p95_ms=200,
        p99_ms=300,
        success_rate_pct=100.0,
    )
    with pytest.raises((AttributeError, TypeError)):
        stats.count = 999  # type: ignore[misc] — frozen-dc mutation must raise; we're proving immutability


# ---------------------------------------------------------------------------
# F30 outcome test — subprocess boot of ``python -m kairix.cli probe mcp-calls``.
# Asserts on stdout/stderr and exit code (not just returncode), per
# F30's "every CLI subcommand has an outcome test asserting on stdout/stderr".
# ---------------------------------------------------------------------------


def test_cli_probe_mcp_calls_subprocess(tmp_path: Path) -> None:
    """End-to-end: invoke ``python -m kairix.cli probe mcp-calls`` and check stdout.

    The subprocess inherits no KAIRIX_* env vars; we point it at a
    tmp DB via the operator-supplied path that the dispatcher walks
    through ``kairix.paths.db_path()``. Since the brief specifies no
    KAIRIX_* env vars in tests, we use --db on the migration first
    then route the subprocess through KAIRIX_DB_PATH... actually
    the CLI doesn't accept --db today, so we set the env var at the
    subprocess boundary (which is F2-allowed because the subprocess
    is a new process, not the test's process).

    Sabotage proof: prepend ``raise SystemExit(1)`` at the top of
    ``kairix.quality.probe.mcp_calls_cli.main`` — the subprocess
    returns 1 and the assertion on rc == 0 fails. Restored.
    """
    # Post 2026-06-04 fix: the observability DB is a sibling of the main
    # index DB, at ``db_path().parent / "mcp_observability.sqlite"``.
    # Set KAIRIX_DB_PATH to a sibling path so the derived observability
    # file lands in tmp_path, then seed THAT file.
    main_db_path = tmp_path / "index.sqlite"
    obs_db_path = tmp_path / "mcp_observability.sqlite"
    _make_db(tmp_path)  # creates the main DB file at the conventional name
    _seed(
        obs_db_path,
        [
            ("2026-06-03T10:00:00Z", "search", "shape", 120, 1, None),
        ],
    )

    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "probe", "mcp-calls"],
        env={"KAIRIX_DB_PATH": str(main_db_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # The operator's report names the tool and the headers.
    assert "search" in result.stdout
    assert "count" in result.stdout
