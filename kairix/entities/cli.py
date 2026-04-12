"""
Entity graph CLI for Mnemosyne.

Usage:
  kairix entity write --name "Alice Chen" --type person [--summary "..."] [--fact "CEO, 2024"]
  kairix entity lookup "Alice Chen"
  kairix entity list [--type person]
  kairix entity extract --changed
  kairix entity extract --all
  kairix entity extract --path file.md
  kairix entity extract --use-llm
  kairix entity reconcile --report
"""

import argparse
import sys


def _write_cmd(args: argparse.Namespace) -> None:
    from kairix.entities.graph import entity_write, set_vault_path, slug
    from kairix.entities.schema import open_entities_db

    db = open_entities_db()
    entity_id = slug(args.name)
    markdown_path = args.markdown_path or f"06-Entities/{args.type}/{entity_id}.md"

    facts = list(args.fact) if args.fact else None

    result_id = entity_write(
        name=args.name,
        entity_type=args.type,
        markdown_path=markdown_path,
        db=db,
        facts=facts,
        summary=args.summary,
    )

    if args.vault_path:
        set_vault_path(result_id, args.vault_path, db)

    db.close()
    print(f"✅ entity written: {result_id}")


def _lookup_cmd(args: argparse.Namespace) -> None:
    from kairix.entities.graph import entity_lookup
    from kairix.entities.schema import open_entities_db

    db = open_entities_db()
    result = entity_lookup(args.name, db)
    db.close()

    if result is None:
        print(f"No entity found matching '{args.name}'", file=sys.stderr)
        sys.exit(1)

    print(f"id:      {result.id}")
    print(f"type:    {result.type}")
    print(f"name:    {result.name}")
    print(f"summary: {result.summary or '—'}")
    print(f"path:    {result.markdown_path}")

    if result.facts:
        print(f"\nFacts ({len(result.facts)}):")
        for f in result.facts:
            print(f"  [{f['fact_type']}] {f['fact_text']}")

    if result.mentions:
        print(f"\nMentions ({len(result.mentions)}):")
        for m in result.mentions:
            print(f"  {m}")

    if result.relationships:
        print(f"\nRelationships ({len(result.relationships)}):")
        for r in result.relationships:
            print(f"  {r['from_entity']} --[{r['rel_type']}]--> {r['to_entity']}")


def _list_cmd(args: argparse.Namespace) -> None:
    from kairix.entities.graph import entity_list
    from kairix.entities.schema import open_entities_db

    db = open_entities_db()
    entities = entity_list(db, entity_type=args.type)
    db.close()

    if not entities:
        print("No entities found.")
        return

    for e in entities:
        summary = f" — {e.summary}" if e.summary else ""
        print(f"[{e.type}] {e.name} ({e.id}){summary}")


# ---------------------------------------------------------------------------
# extract command
# ---------------------------------------------------------------------------


def _extract_cmd(args: argparse.Namespace) -> None:
    """Run NER extraction over vault files."""
    import logging
    import os

    from kairix.entities.pipeline import (
        VAULT_ROOT,
        get_changed_files,
        get_last_run_mtime,
        run_extraction_pipeline,
        set_last_run_mtime,
    )
    from kairix.entities.schema import open_entities_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    db = open_entities_db()

    # Determine which files to process
    if args.path:
        paths = [os.path.abspath(args.path)]
        since_mtime = None
        print(f"🔍 Extracting from single file: {args.path}")
    elif args.changed:
        since_mtime = get_last_run_mtime(db)
        if since_mtime:
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(since_mtime, tz=timezone.utc)
            print(f"🔍 Scanning files changed since {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        else:
            print("🔍 No prior run found — scanning all eligible files")
        paths = get_changed_files(vault_root=VAULT_ROOT, since_mtime=since_mtime)
    else:
        # --all (default)
        since_mtime = None
        print("🔍 Full vault scan (--all mode)")
        paths = get_changed_files(vault_root=VAULT_ROOT, since_mtime=None)

    if not paths:
        print("✅ No files to process.")
        db.close()
        return

    print(f"📄 Processing {len(paths)} file(s)…")

    # API credentials for LLM (from environment)
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")

    summary = run_extraction_pipeline(
        paths=paths,
        db=db,
        use_llm=args.use_llm,
        since_mtime=since_mtime,
        api_key=api_key,
        endpoint=endpoint,
    )

    # Record successful run time (even if some files errored)
    if not args.path:  # don't update last-run for single-file mode
        set_last_run_mtime(db)

    db.close()

    print(
        f"\n✅ Done — {summary['processed']} processed, "
        f"{summary['skipped']} skipped, "
        f"{summary['new_entities']} new entities, "
        f"{summary['merged']} merged, "
        f"{summary['mentions_written']} mentions written, "
        f"{summary['errors']} errors"
    )
    if summary["errors"] > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# reconcile command
# ---------------------------------------------------------------------------


def _reconcile_cmd(args: argparse.Namespace) -> None:
    """Show merge candidates or run reconciliation."""
    import logging

    from kairix.entities.reconcile import _string_similarity
    from kairix.entities.schema import open_entities_db

    logging.basicConfig(level=logging.WARNING)

    db = open_entities_db()

    # Fetch all active entities
    try:
        rows = db.execute("SELECT id, name, type FROM entities WHERE status = 'active' ORDER BY name").fetchall()
    except Exception as exc:
        print(f"❌ Failed to load entities: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    if not rows:
        print("No entities found.")
        db.close()
        return

    # Find near-duplicate pairs
    candidates: list[tuple[float, str, str, str, str]] = []
    entity_list = list(rows)

    for i, row_a in enumerate(entity_list):
        for row_b in entity_list[i + 1 :]:
            score = _string_similarity(row_a["name"], row_b["name"])
            if score >= 0.6:  # show anything with moderate similarity
                candidates.append((score, row_a["id"], row_a["name"], row_b["id"], row_b["name"]))

    candidates.sort(key=lambda x: x[0], reverse=True)

    if not candidates:
        print("✅ No merge candidates found (all entity names are sufficiently distinct).")
        db.close()
        return

    print(f"🔎 Merge candidates ({len(candidates)} pairs):\n")
    for score, id_a, name_a, id_b, name_b in candidates:
        marker = "🔴 AUTO-MERGE" if score >= 0.92 else "🟡 REVIEW"
        print(f"  {marker} [{score:.2f}] '{name_a}' ({id_a}) ↔ '{name_b}' ({id_b})")

    db.close()

    if args.report:
        print("\nInfo: Report only -- no changes written. Use `kairix entity write` to manually merge.")


def main(argv: list[str] | None = None) -> None:
    """Entry point for `kairix entity` subcommand."""
    parser = argparse.ArgumentParser(
        prog="kairix entity",
        description="Entity graph: write, lookup, and list entities.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # --- write ---
    write_parser = subparsers.add_parser("write", help="Create or update an entity")
    write_parser.add_argument("--name", required=True, help="Entity name (e.g. 'Alice Chen')")
    write_parser.add_argument(
        "--type",
        required=True,
        choices=["person", "organisation", "decision", "concept", "project"],
        help="Entity type",
    )
    write_parser.add_argument("--summary", default=None, help="One-line summary")
    write_parser.add_argument(
        "--fact",
        action="append",
        default=None,
        metavar="FACT",
        help="Fact to associate (repeatable, e.g. --fact 'CEO, 2024')",
    )
    write_parser.add_argument(
        "--markdown-path",
        default=None,
        dest="markdown_path",
        help="Vault-relative markdown path (default: 06-Entities/<type>/<slug>.md)",
    )
    write_parser.add_argument(
        "--vault-path",
        default=None,
        dest="vault_path",
        help="Vault folder/file path for wikilink ontology (e.g. '02-Areas/Clients/Acme-Corp/')",
    )
    write_parser.set_defaults(func=_write_cmd)

    # --- lookup ---
    lookup_parser = subparsers.add_parser("lookup", help="Look up an entity by name")
    lookup_parser.add_argument("name", help="Name to search for")
    lookup_parser.set_defaults(func=_lookup_cmd)

    # --- list ---
    list_parser = subparsers.add_parser("list", help="List all entities")
    list_parser.add_argument("--type", default=None, help="Filter by entity type")
    list_parser.set_defaults(func=_list_cmd)

    # --- extract ---
    extract_parser = subparsers.add_parser("extract", help="Run NER extraction over vault files")
    extract_mode = extract_parser.add_mutually_exclusive_group()
    extract_mode.add_argument(
        "--changed",
        action="store_true",
        default=False,
        help="Only process files modified since last run (default for incremental updates)",
    )
    extract_mode.add_argument(
        "--all",
        dest="all",
        action="store_true",
        default=False,
        help="Full vault scan (slow; use for initial population)",
    )
    extract_mode.add_argument(
        "--path",
        default=None,
        metavar="FILE",
        help="Extract from a single file",
    )
    extract_parser.add_argument(
        "--use-llm",
        dest="use_llm",
        action="store_true",
        default=False,
        help="Enable gpt-4o-mini for ambiguous cases (requires AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT)",
    )
    extract_parser.set_defaults(func=_extract_cmd)

    # --- reconcile ---
    reconcile_parser = subparsers.add_parser("reconcile", help="Show or apply entity merge candidates")
    reconcile_parser.add_argument(
        "--report",
        action="store_true",
        default=False,
        help="Show merge candidates without writing (dry-run)",
    )
    reconcile_parser.set_defaults(func=_reconcile_cmd)

    parsed = parser.parse_args(argv)
    parsed.func(parsed)


if __name__ == "__main__":
    main()
