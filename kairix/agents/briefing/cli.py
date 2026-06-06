"""
kairix brief — session briefing synthesis.

Usage:
  kairix brief <agent> [--print] [--json]

Generates a session briefing at the configured briefing dir and prints
the path and first 30 lines to stdout.

Adapter only — business logic lives in
``kairix.use_cases.brief.run_brief``.

PR 1.2 / #420 — the legacy ``--memory-root`` flag has been removed.
The agent's memory + workspace surfaces now come from the ``agents:``
block in ``kairix.config.yaml`` (or the ``agent_defaults:`` synthesis
fallback). Operators that previously passed ``--memory-root`` should
declare the directory under ``agents.<name>.surfaces`` instead — run
``kairix onboard agent --name <agent>`` to scaffold the entry.
"""

from __future__ import annotations

import argparse
import json
import sys

from kairix.use_cases.brief import BriefDeps, BriefOutput, brief_output_to_envelope, run_brief


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix brief",
        description="Generate a session briefing for an agent.",
    )
    parser.add_argument(
        "agent",
        help="Agent name (builder, shape, growth, consultant).",
    )
    parser.add_argument(
        "--print",
        dest="print_output",
        action="store_true",
        default=False,
        help="Print the full briefing to stdout.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help=(
            "Emit the brief envelope dict as JSON to stdout. The same shape "
            "``tool_brief`` returns over MCP — use to drive automation or "
            "to inspect the warm-MCP routing path (#421)."
        ),
    )
    return parser


def format_output(out: BriefOutput, *, print_full: bool) -> str:
    """Render the operator-facing stdout — preview or full content."""
    if not out.content:
        return ""
    if print_full:
        return out.content
    lines = out.content.splitlines()
    preview = "\n".join(lines[:30])
    if len(lines) > 30:
        preview = f"{preview}\n\n... ({len(lines) - 30} more lines — see {out.path})"
    return preview


def main(args: list[str] | None = None, *, deps: BriefDeps | None = None) -> None:
    """Entry point for ``kairix brief``.

    The optional ``deps`` parameter forwards a ``BriefDeps`` directly
    to the use case — production callers leave it None.
    """
    if args is None:
        args = sys.argv[2:]  # strip 'kairix brief'
    parsed = build_parser().parse_args(args)

    print(f"Generating briefing for agent: {parsed.agent} ...", file=sys.stderr)
    out = run_brief(parsed.agent, deps=deps)

    if parsed.as_json:
        # JSON mode short-circuits the human-facing branches: the
        # envelope carries the error / path / content fields the caller
        # needs to parse. Operator-facing stderr trace ("Generating
        # briefing...") stays so the subprocess narration still appears
        # in interactive use, but nothing else writes to stderr/stdout.
        # Exit non-zero on error so shell pipelines can branch on it.
        print(json.dumps(brief_output_to_envelope(out), indent=2))
        if out.error:
            sys.exit(1)
        return

    if out.error:
        print(f"Error generating briefing: {out.error}", file=sys.stderr)
        sys.exit(1)

    if out.path:
        print(f"Briefing written to: {out.path}", file=sys.stderr)

    rendered = format_output(out, print_full=parsed.print_output)
    if rendered:
        print(rendered)  # lgtm[py/clear-text-logging-sensitive-data] — intentional CLI output of user's own briefing
