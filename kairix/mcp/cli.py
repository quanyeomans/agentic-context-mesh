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


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        from kairix.mcp.server import build_server
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    server = build_server(host=args.host, port=args.port)

    if args.transport == "sse":
        print(f"Starting kairix MCP server on {args.host}:{args.port} (SSE transport)", file=sys.stderr)
        server.run(transport="sse")
    else:
        print("Starting kairix MCP server (stdio transport)", file=sys.stderr)
        server.run(transport="stdio")
