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
    args = parser.parse_args(argv)

    from kairix.setup.wizard import run_setup

    success = run_setup(output_path=args.output)
    sys.exit(0 if success else 1)
