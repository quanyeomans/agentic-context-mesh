"""
kairix brief — session briefing synthesis.

Usage:
  kairix brief <agent>

Generates a session briefing at /data/kairix/briefing/<agent>-latest.md
and prints the path and first 30 lines to stdout.
"""

from __future__ import annotations

import sys


def main(args: list[str] | None = None) -> None:
    """Entry point for `kairix brief`."""
    import argparse

    if args is None:
        args = sys.argv[2:]  # strip 'kairix brief'

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

    parsed = parser.parse_args(args)
    agent = parsed.agent.lower().strip()

    valid_agents = {"builder", "shape", "growth", "consultant"}
    if agent not in valid_agents:
        print(
            f"Error: invalid agent {agent!r}. Must be one of: {sorted(valid_agents)}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Generating briefing for agent: {agent} ...", file=sys.stderr)

    try:
        from kairix.agents.briefing.pipeline import generate_briefing
        from kairix.agents.briefing.writer import BRIEFING_DIR

        content = generate_briefing(agent)

        out_path = BRIEFING_DIR / f"{agent}-latest.md"
        print(f"Briefing written to: {out_path}", file=sys.stderr)

        if parsed.print_output:
            print(
                content
            )  # lgtm[py/clear-text-logging-sensitive-data] — intentional: user requested --print-output flag; briefing is user's own document
        else:
            # Print first 30 lines to stdout
            lines = content.splitlines()
            preview = "\n".join(lines[:30])
            print(
                preview
            )  # lgtm[py/clear-text-logging-sensitive-data] — intentional: CLI preview output for user review
            if len(lines) > 30:
                print(f"\n... ({len(lines) - 30} more lines — see {out_path})")

    except Exception as e:
        print(f"Error generating briefing: {e}", file=sys.stderr)
        sys.exit(1)
