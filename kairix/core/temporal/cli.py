"""
kairix timeline — Temporal query rewriting + date-aware retrieval.

Usage:
  kairix timeline <query> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--limit N]
  kairix timeline --help

Examples:
  kairix timeline "what was completed last week on kairix"
  kairix timeline "what happened in March 2026" --since 2026-03-01 --until 2026-03-31
  kairix timeline "recent Bower Bird changes" --limit 10
"""

from __future__ import annotations

import argparse
import sys
from datetime import date


def main(argv: list[str] | None = None) -> None:
    """Entry point for `kairix timeline`."""
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
        metavar="YYYY-MM-DD",
        help="Override start date (ISO format). If omitted, extracted from query.",
    )
    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
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

    args = parser.parse_args(argv if argv is not None else sys.argv[2:])

    # Lazy imports
    from kairix.core.temporal.rewriter import extract_time_window, rewrite_temporal_query

    # Determine date window
    start: date | None = None
    end: date | None = None

    if args.since:
        try:
            start = date.fromisoformat(args.since)
        except ValueError:
            print(f"error: invalid --since date: {args.since!r}", file=sys.stderr)
            sys.exit(1)

    if args.until:
        try:
            end = date.fromisoformat(args.until)
        except ValueError:
            print(f"error: invalid --until date: {args.until!r}", file=sys.stderr)
            sys.exit(1)

    # If no explicit dates, extract from query
    if start is None and end is None:
        start, end = extract_time_window(args.query)

    # Rewrite query for search
    rewritten = rewrite_temporal_query(args.query)

    # Chunk type filter
    chunk_types: list[str] | None = None
    if args.chunk_type != "all":
        chunk_types = [args.chunk_type]

    # Print query context
    print(f"Query:    {args.query}")
    print(f"Rewritten: {rewritten}")
    if start or end:
        start_str = start.isoformat() if start else "earliest"
        end_str = end.isoformat() if end else "latest"
        print(f"Window:   {start_str} → {end_str}")
    else:
        print("Window:   (no date filter — showing all)")
    print(f"Limit:    {args.limit}")
    print()

    # Run query
    from kairix.core.temporal.index import query_temporal_chunks

    results = query_temporal_chunks(
        topic=rewritten,
        start=start,
        end=end,
        chunk_types=chunk_types,
        limit=args.limit,
    )

    if not results:
        print("No results found.")
        return

    print(f"Found {len(results)} result(s):\n")

    for i, chunk in enumerate(results, 1):
        date_str = chunk.date.isoformat() if chunk.date else "undated"
        source = chunk.source_path
        chunk_type = chunk.chunk_type

        # Metadata summary
        meta_parts: list[str] = []
        if chunk_type == "board_card":
            if "status" in chunk.metadata:
                meta_parts.append(f"status={chunk.metadata['status']}")
            if "column" in chunk.metadata:
                meta_parts.append(f"column={chunk.metadata['column']!r}")
        elif chunk_type == "memory_section":
            if chunk.metadata.get("section_heading"):
                meta_parts.append(f"section={chunk.metadata['section_heading']!r}")

        meta_str = "  " + ", ".join(meta_parts) if meta_parts else ""

        print(f"[{i}] {date_str}  {chunk_type}{meta_str}")
        print(f"     Source: {source}")
        # Show first 200 chars of text
        preview = chunk.text.replace("\n", " ")[:200]
        if len(chunk.text) > 200:
            preview += "…"
        print(f"     {preview}")
        print()
