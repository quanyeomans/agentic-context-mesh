"""kairix entities CLI — entity management commands."""

from __future__ import annotations

import argparse
import sys


def cmd_suggest(args: argparse.Namespace) -> int:
    """kairix entity suggest <text> — NER-based entity suggestions."""
    from kairix.entities.suggest import format_suggestions, suggest_entities
    from kairix.graph.client import get_client

    text = args.text
    if args.file:
        from pathlib import Path

        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        neo4j = get_client()
        suggestions = suggest_entities(text, neo4j)
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(format_suggestions(suggestions, fmt=args.format))

    if args.format == "table":
        new_count = sum(1 for s in suggestions if s.is_new)
        existing_count = len(suggestions) - new_count
        print(f"\nTotal: {len(suggestions)} entities found ({new_count} new, {existing_count} existing)")

    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """kairix entity validate <name> — validate entity against Wikidata."""
    import json as _json

    from kairix.entities.validate import validate_entity
    from kairix.graph.client import get_client

    neo4j = get_client()
    result = validate_entity(args.name, neo4j, update=args.update)

    if args.format == "json":
        print(_json.dumps(result, indent=2))
        return 0

    # Table output
    print(f"\nEntity: {result['name']}")
    print(f"Neo4j id: {result['neo4j_id'] or '(not found)'}")
    if result["updated"]:
        print("Updated: wikidata_qid written to Neo4j node")
    print()

    if not result["matches"]:
        print("No Wikidata matches found.")
        return 0

    print(f"{'QID':<12} {'CONFIDENCE':<12} {'LABEL':<30} DESCRIPTION")
    print("-" * 90)
    for m in result["matches"]:
        print(f"{m['qid']:<12} {m['confidence']:<12} {m['label']:<30} {m['description'][:35]}")

    print(f"\nBest match: {result['matches'][0]['url']}")
    if not args.update and result["matches"] and result["matches"][0]["confidence"] in ("high", "medium"):
        print("Run with --update to write wikidata_qid to Neo4j.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix entity",
        description="Entity management commands",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # suggest subcommand
    p_suggest = sub.add_parser("suggest", help="Suggest new entities using NER")
    p_suggest.add_argument("text", nargs="?", default="", help="Text to analyse (or use --file)")
    p_suggest.add_argument("--file", "-f", default=None, help="Read text from file")
    p_suggest.add_argument(
        "--format", choices=["table", "jsonl"], default="table", help="Output format (default: table)"
    )
    p_suggest.set_defaults(func=cmd_suggest)

    # validate subcommand
    p_validate = sub.add_parser("validate", help="Validate entity against Wikidata")
    p_validate.add_argument("name", help="Entity name to look up")
    p_validate.add_argument("--update", action="store_true", help="Write wikidata_qid to Neo4j node")
    p_validate.add_argument("--format", choices=["table", "json"], default="table")
    p_validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result
