"""
kairix.mcp.cli — CLI entry point for the MCP server.

Usage:
    kairix mcp serve [--port PORT] [--transport stdio|sse]

Requires kairix[agents]: pip install 'kairix[agents]'
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kairix mcp",
        description="MCP server: expose search/entity/prep/timeline as MCP tools",
    )
    sub = parser.add_subparsers(dest="subcommand")

    serve_p = sub.add_parser("serve", help="Start the MCP server")
    serve_p.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on when transport=sse (default: 8080)",
    )
    serve_p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to when transport=sse (default: 127.0.0.1). "
        "WARNING: The MCP server has no authentication. Do not bind to 0.0.0.0 "
        "unless you have network-level access controls in place.",
    )
    serve_p.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport: stdio (default, for Claude Desktop) or sse (HTTP + SSE)",
    )

    args = parser.parse_args(argv)

    if args.subcommand == "serve":
        _cmd_serve(args)
    else:
        parser.print_help()
        sys.exit(1)


def _resolve_port(args: argparse.Namespace) -> int:
    """Resolve MCP port: CLI flag → env var → config → auto-detect."""
    import os

    # CLI flag takes precedence (argparse default is 8080)
    if "--port" in sys.argv:
        return int(args.port)

    # Environment variable
    env_port = os.environ.get("KAIRIX_MCP_PORT")
    if env_port:
        return int(env_port)

    # Auto-detect: check if default port is available
    from kairix.onboard.ports import find_available_port, is_port_available

    default = 8080
    if is_port_available(default):
        return default

    suggested = find_available_port(preferred=default)
    print(
        f"Port {default} is in use — using {suggested} instead. "
        f"Set KAIRIX_MCP_PORT={suggested} to make this permanent.",
        file=sys.stderr,
    )
    return suggested


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        from kairix.mcp.server import build_server
    except ImportError:
        print("Error: MCP dependencies not installed. Run: pip install 'kairix[agents]'", file=sys.stderr)
        sys.exit(1)

    port = _resolve_port(args) if args.transport == "sse" else args.port
    server = build_server(host=args.host, port=port)

    if args.transport == "sse":
        print(f"Starting kairix MCP server on {args.host}:{port} (SSE transport)", file=sys.stderr)
        server.run(transport="sse")
    else:
        print("Starting kairix MCP server (stdio transport)", file=sys.stderr)
        server.run(transport="stdio")
