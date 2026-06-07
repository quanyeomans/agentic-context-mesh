"""argparse subcommand bodies for ``kairix onboard scan`` and
``kairix onboard agent`` (PR 1.4 / #420).

These functions are wired into the top-level ``kairix onboard`` argparse
dispatcher in :mod:`kairix.platform.onboard.cli`. Keeping the
implementation here (under ``kairix.agents.onboarding``) instead of in
the platform package keeps the agent-config domain logic colocated with
the scanner + renderer; the platform layer only knows the routing
shape.

Both subcommands support ``--json`` so the warm-MCP routing path
introduced by PR 2.8 picks them up immediately. Default output is the
operator-readable validation report; ``--yaml`` emits the paste-ready
config block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from kairix.agents.onboarding.renderer import (
    render_scopes_as_yaml,
    render_validation_report,
)
from kairix.agents.onboarding.scanner import (
    ProposedScope,
    discover_single_agent,
    scan_for_agents,
)

# F17 — argparse action keyword repeated across boolean-flag declarations.
_STORE_TRUE = "store_true"


def add_scan_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the ``onboard scan`` subparser onto an existing add_subparsers."""
    parser = sub.add_parser(
        "scan",
        help="Discover agent scopes on disk and propose kairix.config.yaml blocks.",
    )
    parser.add_argument(
        "--memory-root",
        required=True,
        help="Root directory containing agent subdirectories (one subdir per agent).",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Optional sibling root containing per-agent workspace subdirectories.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action=_STORE_TRUE,
        default=False,
        help="Emit the discovery result as a JSON envelope (same shape as tool_onboard_scan).",
    )
    parser.add_argument(
        "--yaml",
        dest="as_yaml",
        action=_STORE_TRUE,
        default=False,
        help="Emit a paste-ready kairix.config.yaml `agents:` block.",
    )


def add_agent_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the ``onboard agent`` subparser onto an existing add_subparsers."""
    parser = sub.add_parser(
        "agent",
        help="Discover surfaces for one named agent (single-target counterpart to `scan`).",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Agent name (matches the subdirectory under --memory-root).",
    )
    parser.add_argument(
        "--memory-root",
        required=True,
        help="Root directory containing the agent subdirectory.",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Optional sibling root containing the agent's workspace subdirectory.",
    )
    parser.add_argument(
        "--harness",
        default=None,
        help="Limit detection to one harness (claude-code, codex, generic).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action=_STORE_TRUE,
        default=False,
        help="Emit the discovery result as a JSON envelope (same shape as tool_onboard_agent).",
    )
    parser.add_argument(
        "--yaml",
        dest="as_yaml",
        action=_STORE_TRUE,
        default=False,
        help="Emit a paste-ready kairix.config.yaml `agents:` block for the one agent.",
    )


def scope_to_envelope(scope: ProposedScope) -> dict[str, object]:
    """Render one ProposedScope as the JSON envelope shape used by the
    CLI ``--json`` path AND the MCP ``tool_onboard_*`` tools."""
    return {
        "name": scope.name,
        "harness": scope.harness,
        "confidence": scope.confidence,
        "file_count": scope.file_count,
        "most_recent_mtime": scope.most_recent_mtime,
        "surfaces": [{"path": str(s.path), "glob": s.glob, "label": s.label} for s in scope.surfaces],
    }


def cmd_scan(args: argparse.Namespace) -> int:
    """Execute ``kairix onboard scan``.

    Returns 0 when scopes were discovered, 1 when the scan returned an
    empty result (operator-actionable signal: the memory_root contains no
    agent-shaped subdirectories, so the proposed config block would be
    empty). The non-trivial return value lets pipelines fail-fast on
    misconfigured discovery roots instead of silently producing empty
    config blocks.
    """
    memory_root = Path(args.memory_root)
    workspace_root = Path(args.workspace_root) if args.workspace_root else None
    scopes = scan_for_agents(memory_root=memory_root, workspace_root=workspace_root)

    if args.as_json:
        envelope = {
            "agents": [scope_to_envelope(s) for s in scopes],
            "error": "",
        }
        print(json.dumps(envelope, indent=2))
    elif args.as_yaml:
        print(render_scopes_as_yaml(scopes), end="")
    else:
        print(render_validation_report(scopes), end="")

    return 0 if scopes else 1


def cmd_agent(args: argparse.Namespace) -> int:
    """Execute ``kairix onboard agent``."""
    memory_root = Path(args.memory_root)
    workspace_root = Path(args.workspace_root) if args.workspace_root else None
    try:
        scope = discover_single_agent(
            args.name,
            memory_root=memory_root,
            workspace_root=workspace_root,
            harness=args.harness,
        )
    except ValueError as exc:
        if args.as_json:
            err_envelope: dict[str, Any] = {"agent": None, "error": str(exc)}
            print(json.dumps(err_envelope, indent=2))
        else:
            print(f"onboard agent failed: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        ok_envelope: dict[str, Any] = {"agent": scope_to_envelope(scope), "error": ""}
        print(json.dumps(ok_envelope, indent=2))
        return 0

    if args.as_yaml:
        print(render_scopes_as_yaml((scope,)), end="")
        return 0

    print(render_validation_report((scope,)), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m kairix.agents.onboarding.cli``.

    Standalone driver — useful for tests that want to drive the parser
    without the top-level ``kairix onboard`` dispatcher. Production
    callers route through :mod:`kairix.platform.onboard.cli`'s ``main``
    which wires the same subparsers into the shared ``kairix onboard``
    namespace.
    """
    parser = argparse.ArgumentParser(
        prog="kairix.agents.onboarding",
        description="Agent scope discovery + proposal.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    add_scan_parser(sub)
    add_agent_parser(sub)
    args = parser.parse_args(argv)
    if args.subcommand == "scan":
        return cmd_scan(args)
    if args.subcommand == "agent":
        return cmd_agent(args)
    return 2
