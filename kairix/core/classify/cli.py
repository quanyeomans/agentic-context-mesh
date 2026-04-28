"""
kairix classify — auto-classify memory writes.

Usage:
  kairix classify "<content>" [--agent <agent>]
  echo "<content>" | kairix classify --agent builder

Output: JSON to stdout
  {"type": "...", "target_path": "...", "confidence": 0.xx, "reason": "..."}
  {"type": "...", "target_path": "...", "confidence": 0.xx, "reason": "...", "needs_confirmation": true}
"""

from __future__ import annotations

import json
import sys


def main(args: list[str] | None = None) -> None:
    """Entry point for `kairix classify`."""
    import argparse

    if args is None:
        args = sys.argv[2:]  # strip 'kairix classify'

    parser = argparse.ArgumentParser(
        prog="kairix classify",
        description="Auto-classify memory writes to the correct document path.",
    )
    parser.add_argument(
        "content",
        nargs="?",
        default=None,
        help="Content to classify (or pipe via stdin).",
    )
    parser.add_argument(
        "--agent",
        default="shared",
        help="Agent name for path scoping (builder, shape, growth, consultant, shared).",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        default=False,
        help="Disable LLM fallback — return unknown if no rule matches.",
    )

    parsed = parser.parse_args(args)

    # Get content
    content = parsed.content
    if content is None:
        if not sys.stdin.isatty():
            content = sys.stdin.read()
        else:
            print("Error: no content provided (pass as argument or pipe via stdin)", file=sys.stderr)
            sys.exit(1)

    agent = parsed.agent
    use_llm = not parsed.no_llm

    # Run classification
    try:
        from kairix.core.classify.judge import classify_with_llm
        from kairix.core.classify.rules import VALID_AGENTS, classify_content

        if agent not in VALID_AGENTS:
            print(
                f"Error: invalid agent {agent!r}. Must be one of: {sorted(VALID_AGENTS)}",
                file=sys.stderr,
            )
            sys.exit(1)

        result = classify_content(content, agent=agent)

        # If rule didn't match, try LLM judge
        if result.type == "unknown" and use_llm:
            result = classify_with_llm(content, agent=agent)

        output: dict = {
            "type": result.type,
            "target_path": result.target_path,
            "confidence": round(result.confidence, 2),
            "reason": result.reason,
        }
        if result.needs_confirmation:
            output["needs_confirmation"] = True

        print(json.dumps(output))

    except ValueError as e:
        print(json.dumps({"error": "Classification failed — check server logs"}), file=sys.stderr)
        import logging as _logging

        _logging.getLogger(__name__).warning("classify CLI ValueError: %s", e)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": "Classification failed — check server logs"}), file=sys.stderr)
        import logging as _logging

        _logging.getLogger(__name__).warning("classify CLI unexpected error: %s", e)
        sys.exit(1)
