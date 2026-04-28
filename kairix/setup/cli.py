"""CLI entry point for the kairix setup wizard."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kairix setup",
        description="Interactive setup wizard — configures LLM, documents, and search in a few steps",
    )
    parser.add_argument(
        "--output",
        default="kairix.config.yaml",
        help="Output config file path (default: kairix.config.yaml)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip all prompts, use defaults (for CI/Docker/scripting)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output config as JSON to stdout instead of writing YAML file",
    )
    parser.add_argument(
        "--preset",
        choices=["consulting", "technical", "daily-log", "general", "agent-memory", "exploring"],
        default=None,
        help="Use a preset configuration (skips the use-case survey)",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Document root path (skips the document source prompt)",
    )
    args = parser.parse_args(argv)

    from kairix.setup.prompts import SetupContext
    from kairix.setup.wizard import run_setup

    ctx = SetupContext.auto_detect(
        non_interactive=args.non_interactive,
        json_mode=args.json,
    )

    success = run_setup(
        ctx=ctx,
        output_path=args.output,
        preset=args.preset,
        document_path=args.path,
    )
    sys.exit(0 if success else 1)
