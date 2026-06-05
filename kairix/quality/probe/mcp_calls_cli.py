"""``kairix mcp-calls`` — operator surface over the mcp_call_log table.

Issue #398 (Workstream D). Surfaces per-MCP-tool-call observability
written by ``kairix.agents.mcp.errors.async_tool_handler`` into
``mcp_call_log`` (one row per call). Operators run this to investigate
latency tails, error rates, and per-tool call volume.

The CLI shape:

  kairix mcp-calls [--since DURATION] [--tool NAME] [--json]

Text mode (default): one row per tool with count, p50/p95/p99
latency (ms), success rate (%), and the top-3 error classes.

JSON mode: emits a dict keyed by tool, suitable for piping into
``jq`` or downstream tooling.

The implementation is read-only (SELECT only) — no risk of corrupting
the call log. A missing ``mcp_call_log`` table surfaces an F21-shaped
error (operator hasn't run the migration yet).

Wire-up: this module's ``main()`` is dispatched directly from the top-level
``kairix.cli.COMMANDS`` table when the user runs ``kairix mcp-calls``.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Duration suffix → seconds. Supports h/m/s/d so operators can type
# ``--since 1h`` or ``--since 30m`` or ``--since 2d``.
_DURATION_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

# Default window when the operator passes no ``--since`` — show the
# full table (no lower bound on timestamp).
_DEFAULT_SINCE_TIMESTAMP = ""

_DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")


@dataclass(frozen=True)
class ToolStats:
    """Per-tool latency + success statistics over a time window.

    Frozen-dc so the projection from SQL rows is immutable and
    easy to envelope as JSON for the ``--json`` branch.
    """

    tool: str
    count: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    success_rate_pct: float
    top_errors: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class McpCallsDeps:
    """Injectable dependencies for :func:`main`.

    Mirrors :class:`kairix.agents.mcp.errors.AsyncToolHandlerDeps` —
    every field is non-Optional with a ``default_factory`` returning
    the production helper. Tests pass an alternate ``db_path_fn``
    closure to route reads to a tmp-path SQLite without setting
    ``KAIRIX_DB_PATH``.
    """

    db_path_fn: Callable[[], Path] = field(default_factory=lambda: _default_db_path)


def _default_db_path() -> Path:
    """Production resolver — points at the dedicated observability SQLite file.

    Sibling to the main index DB. Must match the path
    ``kairix.agents.mcp.errors._default_db_path`` writes to —
    ``/data/kairix/mcp_observability.sqlite`` by default. The dedicated
    file avoids write-lock contention against the worker's writes on
    the main index DB.
    """
    from kairix.paths import db_path as _db_path

    return _db_path().parent / "mcp_observability.sqlite"


def _parse_since(since: str | None) -> str:
    """Parse a ``--since`` value into an ISO8601 lower-bound timestamp.

    Accepts the duration shapes ``Ns``/``Nm``/``Nh``/``Nd``. Returns
    the lower-bound timestamp formatted to match the writer's
    ``isoformat().replace("+00:00", "Z")`` shape so the SQL
    comparison is well-defined.

    Empty / None / ``""`` returns ``""`` meaning "no lower bound".

    Raises ValueError on malformed input — caller surfaces it as an
    F21-shaped error.
    """
    if not since:
        return _DEFAULT_SINCE_TIMESTAMP
    m = _DURATION_PATTERN.match(since.strip())
    if not m:
        raise ValueError(
            f"--since must be a duration like '1h' or '30m' or '7d'; got {since!r}. "
            f"fix: pass --since with a positive integer + s/m/h/d suffix."
        )
    n = int(m.group(1))
    unit = m.group(2)
    seconds = n * _DURATION_UNITS[unit]
    lower_bound = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return lower_bound.isoformat().replace("+00:00", "Z")


def _percentile(values: list[int], pct: float) -> int:
    """Return the integer percentile of ``values`` for ``pct`` in [0, 100].

    Linear interpolation between consecutive values; empty input
    returns 0 (the CLI surfaces an empty-window message before
    reaching this function in that case).
    """
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    sorted_vals = sorted(values)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lower_idx = int(rank)
    upper_idx = min(lower_idx + 1, len(sorted_vals) - 1)
    frac = rank - lower_idx
    return int(sorted_vals[lower_idx] + frac * (sorted_vals[upper_idx] - sorted_vals[lower_idx]))


def _build_tool_stats(rows: list[tuple[str, int, int, str | None]]) -> list[ToolStats]:
    """Project rows into per-tool ToolStats.

    Each input row is ``(tool, latency_ms, success, error_class)``.
    Output is one ToolStats per distinct tool, sorted descending by
    call count.
    """
    by_tool: dict[str, list[tuple[int, int, str | None]]] = {}
    for tool, latency_ms, success, error_class in rows:
        by_tool.setdefault(tool, []).append((int(latency_ms), int(success), error_class))

    stats: list[ToolStats] = []
    for tool, tool_rows in by_tool.items():
        latencies = [latency for latency, _, _ in tool_rows]
        successes = sum(s for _, s, _ in tool_rows)
        count = len(tool_rows)
        success_rate = (100.0 * successes / count) if count else 0.0
        error_counts = Counter(ec for _, s, ec in tool_rows if not s and ec)
        top_errors = error_counts.most_common(3)
        stats.append(
            ToolStats(
                tool=tool,
                count=count,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                success_rate_pct=round(success_rate, 1),
                top_errors=top_errors,
            )
        )
    stats.sort(key=lambda s: s.count, reverse=True)
    return stats


def _query_rows(
    db_path: Path,
    *,
    since_timestamp: str,
    tool_filter: str | None,
) -> list[tuple[str, int, int, str | None]]:
    """Read ``mcp_call_log`` rows under the operator-supplied filters.

    Returns a list of ``(tool, latency_ms, success, error_class)``.

    The query is bounded by a hard LIMIT to avoid runaway scans on
    a large call log — operators iterating on filters get bounded
    latency. The LIMIT marker is documented as F63-bounded.
    """
    # Read-only operator CLI; SELECT only, no UPDATE/DELETE. Never runs
    # inside the worker tick loop — opens its own connection, reads,
    # closes, returns. The writer-coordinator discipline is for write
    # paths; read-only diagnostics don't contend with it.
    conn = sqlite3.connect(str(db_path))  # F77-allow: read-only operator diagnostic; never writes
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if since_timestamp:
            clauses.append("timestamp >= ?")
            params.append(since_timestamp)
        if tool_filter:
            clauses.append("tool = ?")
            params.append(tool_filter)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # F63-bounded: 1M rows is enough to cover ~weeks of an active
        # MCP server's traffic; operators reduce window via --since if
        # they hit the cap.
        sql = (
            f"SELECT tool, latency_ms, success, error_class FROM mcp_call_log {where} "
            "ORDER BY id DESC LIMIT 1000000"  # F63-bounded: 1M-row safety cap
        )
        return [(str(r[0]), int(r[1]), int(r[2]), r[3]) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _format_text(stats: list[ToolStats], *, since: str, tool_filter: str | None) -> str:
    """Render the text-mode report.

    One row per tool: name, count, p50/p95/p99 latency, success rate,
    top-3 error classes.
    """
    if not stats:
        filter_blurb = []
        if since:
            filter_blurb.append(f"--since {since}")
        if tool_filter:
            filter_blurb.append(f"--tool {tool_filter}")
        suffix = (" with " + " ".join(filter_blurb)) if filter_blurb else ""
        return f"mcp-calls: no calls recorded{suffix}.\n"

    longest_tool = max(len(s.tool) for s in stats)
    lines = ["kairix mcp-calls"]
    header = (
        f"  {'tool'.ljust(longest_tool)}  "
        f"{'count'.rjust(6)}  "
        f"{'p50ms'.rjust(7)}  {'p95ms'.rjust(7)}  {'p99ms'.rjust(7)}  "
        f"{'ok%'.rjust(5)}  top_errors"
    )
    lines.append(header)
    for s in stats:
        errors_str = ", ".join(f"{name}({n})" for name, n in s.top_errors) if s.top_errors else "-"
        lines.append(
            f"  {s.tool.ljust(longest_tool)}  "
            f"{s.count:6d}  "
            f"{s.p50_ms:7d}  {s.p95_ms:7d}  {s.p99_ms:7d}  "
            f"{s.success_rate_pct:5.1f}  {errors_str}"
        )
    return "\n".join(lines) + "\n"


def _envelope_for_json(stats: list[ToolStats]) -> dict[str, Any]:
    """Build the JSON-mode envelope.

    Top-level key ``tools`` is a list of dicts; the order matches the
    text-mode output (descending by count) so operators piping into
    ``jq`` see the same priority.
    """
    return {
        "tools": [
            {
                "tool": s.tool,
                "count": s.count,
                "p50_ms": s.p50_ms,
                "p95_ms": s.p95_ms,
                "p99_ms": s.p99_ms,
                "success_rate_pct": s.success_rate_pct,
                "top_errors": [{"class": cls, "count": n} for cls, n in s.top_errors],
            }
            for s in stats
        ]
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix mcp-calls",
        description=(
            "Inspect the mcp_call_log per-tool observability table. "
            "Run this when a dogfood report flags a brief failure or "
            "search latency tail to see which tool is slow + which "
            "error classes are firing."
        ),
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "lower bound on call timestamp as a duration ('1h', '30m', '7d', '300s'). "
            "Default: no lower bound (the whole table)."
        ),
    )
    parser.add_argument(
        "--tool",
        default=None,
        help="filter to a single tool name (e.g. 'search', 'brief').",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit JSON envelope on stdout; suppress human-readable output.",
    )
    return parser


def _print_error(reason: str) -> int:
    """Emit an F21-shaped operator error and return exit code 2."""
    print(f"error: {reason}", file=sys.stderr)
    print(
        "fix: pass --since with a positive integer + s/m/h/d suffix (e.g. '1h', '30m').",
        file=sys.stderr,
    )
    print("next: see `kairix mcp-calls --help` for the accepted shape.", file=sys.stderr)
    print("run: kairix mcp-calls --help", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, *, deps: McpCallsDeps | None = None) -> int:
    """Entry point dispatched from ``kairix.cli.COMMANDS``.

    Args:
        argv: argv slice after the ``mcp-calls`` token; None means
              the parser reads from sys.argv directly.
        deps: Optional dependency container for tests (the canonical
              Deps pattern); production leaves None and the default
              factory wires ``kairix.paths.db_path``.

    Returns:
        0 — report emitted (even when the window is empty).
        2 — invalid args (bad --since, malformed --tool).
    """
    args = _build_parser().parse_args(argv)
    resolved_deps = deps if deps is not None else McpCallsDeps()

    try:
        since_timestamp = _parse_since(args.since)
    except ValueError as exc:
        return _print_error(str(exc))

    try:
        db_path = resolved_deps.db_path_fn()
    except Exception as exc:
        return _print_error(f"could not resolve DB path: {type(exc).__name__}: {exc}")

    try:
        rows = _query_rows(db_path, since_timestamp=since_timestamp, tool_filter=args.tool)
    except sqlite3.OperationalError as exc:
        # Two shapes:
        #   1. The observability DB file doesn't exist yet (fresh deploy,
        #      no MCP traffic has run). Honest answer: "no calls yet".
        #   2. The file exists but the table doesn't (legacy operator
        #      writeup or a different mcp-side bug). Same answer — the
        #      table is now created on first INSERT by
        #      ``kairix.agents.mcp.errors._record_mcp_call``, so a
        #      "missing table" reading is just "no calls yet" too.
        if "no such table" in str(exc).lower() or not db_path.exists():
            rows = []
        else:
            return _print_error(
                f"mcp_call_log read failed: {exc}. "
                "fix: check the observability DB file is readable. "
                "next: ls -la $(kairix paths db | sed 's|/[^/]*$|/mcp_observability.sqlite|')."
            )

    stats = _build_tool_stats(rows)

    if args.as_json:
        print(json.dumps(_envelope_for_json(stats), indent=2))
    else:
        sys.stdout.write(_format_text(stats, since=args.since or "", tool_filter=args.tool))
    return 0


__all__ = ["McpCallsDeps", "ToolStats", "main"]
