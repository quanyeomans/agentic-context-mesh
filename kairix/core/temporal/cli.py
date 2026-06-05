"""
kairix timeline — Temporal query rewriting + date-aware retrieval.

Usage:
  kairix timeline <query> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--limit N]
  kairix timeline --help

Examples:
  kairix timeline "what was completed last week on kairix"
  kairix timeline "what happened in March 2026" --since 2026-03-01 --until 2026-03-31
  kairix timeline "recent Bower Bird changes" --limit 10

Adapter only — business logic lives in ``kairix.use_cases.timeline.run_timeline``.
``main()`` is a thin orchestrator; all rendering is in pure helpers below so
unit tests don't need to capture stdout or stub the use case.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from typing import Any

from kairix.use_cases.timeline import TimelineResult

_ISO_DATE_METAVAR = "YYYY-MM-DD"  # F17 — referenced by --since/--until/--anchor-date


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser used by ``main``. Pure factory — exposed
    for unit tests that want to drive argument parsing without invoking I/O.
    """
    parser = argparse.ArgumentParser(
        prog="kairix timeline",
        description="Temporal query over Kanban boards and daily memory logs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kairix timeline "what was completed last week on kairix"
  kairix timeline "what happened in March 2026" --since 2026-03-01 --until 2026-03-31
  kairix timeline "recent Bower Bird changes" --limit 10
""",
    )
    parser.add_argument("query", help="Temporal query string")
    parser.add_argument(
        "--since",
        metavar=_ISO_DATE_METAVAR,
        help="Override start date (ISO format). If omitted, extracted from query.",
    )
    parser.add_argument(
        "--until",
        metavar=_ISO_DATE_METAVAR,
        help="Override end date (ISO format). If omitted, extracted from query.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of results to return (default: 10)",
    )
    parser.add_argument(
        "--type",
        choices=["board_card", "memory_section", "all"],
        default="all",
        dest="chunk_type",
        help="Filter chunk type (default: all)",
    )
    # CLI ↔ MCP parity (#402): match tool_timeline's agent + anchor_date + json kwargs.
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent name for scope-aware retrieval (matches MCP tool_timeline's ``agent`` kwarg).",
    )
    parser.add_argument(
        "--anchor-date",
        metavar=_ISO_DATE_METAVAR,
        dest="anchor_date",
        help=(
            "Anchor date used by the temporal rewriter when the query lacks an "
            "explicit window (matches MCP tool_timeline's ``anchor_date`` kwarg). "
            "Defaults to today."
        ),
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit a JSON envelope on stdout instead of human-readable text.",
    )
    return parser


def parse_iso_or_die(value: str | None, flag_name: str) -> date | None:
    """Parse an ISO date or print an error + sys.exit(1) on failure.

    Pure-ish helper: side-effect is printing to stderr + exit. Tests should
    catch ``SystemExit`` to assert the exit-on-bad-input contract.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        print(f"error: invalid {flag_name} date: {value!r}", file=sys.stderr)
        sys.exit(1)


def format_header(result: TimelineResult, limit: int) -> str:
    """Render the query/window/limit banner that prefixes every CLI run."""
    lines: list[str] = [
        f"Query:    {result.original_query}",
        f"Rewritten: {result.rewritten_query}",
    ]
    if result.time_window:
        start_str = result.time_window.get("start") or "earliest"
        end_str = result.time_window.get("end") or "latest"
        lines.append(f"Window:   {start_str} → {end_str}")
    else:
        lines.append("Window:   (no date filter — showing all)")
    lines.append(f"Limit:    {limit}")
    if result.fell_back:
        lines.append("Note:     primary temporal index empty — showing search-pipeline fallback")
    return "\n".join(lines)


def format_results(result: TimelineResult) -> str:
    """Render the result list (or the empty-results notice).

    Returns a string ready to ``print``; tests assert on the rendered form.
    """
    if not result.results:
        return "No results found."

    lines: list[str] = [f"Found {len(result.results)} result(s):", ""]
    for i, hit in enumerate(result.results, 1):
        date_str = hit.date or "undated"
        type_str = hit.chunk_type or "search"
        header_line = f"[{i}] {date_str}  {type_str}  {hit.title}".rstrip()
        preview = hit.snippet.replace("\n", " ")[:200]
        if len(hit.snippet) > 200:
            preview += "…"
        lines.extend([header_line, f"     Source: {hit.path}", f"     {preview}", ""])
    return "\n".join(lines)


def _default_timeline_runner(*args: Any, **kwargs: Any) -> Any:
    """Production timeline runner — defers the heavy use-case import until call time."""
    from kairix.use_cases.timeline import run_timeline

    return run_timeline(*args, **kwargs)


def _result_to_envelope(result: Any, *, limit: int) -> dict[str, Any]:
    """Project a ``TimelineResult`` onto the CLI's ``--json`` envelope.

    Delegates to ``timeline_output_to_envelope`` (the canonical SoT
    shared with MCP ``tool_timeline``, #412) and overlays the CLI-only
    ``limit`` field. Pre-#412 this function dropped ``results`` and
    emitted only ``results_count`` — operators calling ``--json`` got
    the hit count but lost the actual hits and had to re-run via MCP
    or parse text mode to recover them.
    """
    from kairix.use_cases.timeline import timeline_output_to_envelope

    envelope = timeline_output_to_envelope(result)
    envelope["limit"] = limit
    return envelope


def main(
    argv: list[str] | None = None,
    *,
    timeline_runner: Callable[..., Any] = _default_timeline_runner,
) -> None:
    """Entry point for ``kairix timeline``.

    Thin adapter: parse argv → call the configured timeline runner →
    format the ``TimelineResult`` for stdout. CLI/MCP parity is enforced
    by the contract test in
    ``tests/contracts/test_cli_mcp_parity_timeline.py``.

    The ``timeline_runner`` kwarg is the public DI seam — tests pass a
    fake runner to drive the CLI without monkey-patching the
    ``kairix.use_cases.timeline`` module.
    """
    args = build_parser().parse_args(argv if argv is not None else sys.argv[2:])

    since = parse_iso_or_die(args.since, "--since")
    until = parse_iso_or_die(args.until, "--until")
    anchor = parse_iso_or_die(args.anchor_date, "--anchor-date")
    chunk_types: list[str] | None = [args.chunk_type] if args.chunk_type != "all" else None

    # CLI ↔ MCP parity (#402): pass agent + anchor_date through identically
    # to how tool_timeline does in kairix/agents/mcp/server.py.
    result = timeline_runner(
        args.query,
        since=since,
        until=until,
        chunk_types=chunk_types,
        limit=args.limit,
        agent=args.agent,
        anchor_date=anchor,
    )

    if args.as_json:
        import json as _json

        envelope = _result_to_envelope(result, limit=args.limit)
        print(_json.dumps(envelope, indent=2, default=str))
        if envelope["error"]:
            sys.exit(1)
        return

    print(format_header(result, args.limit))
    print()
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
    print(format_results(result))
