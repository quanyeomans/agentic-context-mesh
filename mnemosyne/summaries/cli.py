"""
CLI for mnemosyne summarise subcommand.

Usage:
  mnemosyne summarise --all               Generate L0 for all vault docs
  mnemosyne summarise --stale             Regenerate only stale/missing
  mnemosyne summarise --path FILE         Single file
  mnemosyne summarise --all --include-l1  Generate both L0 + L1
  mnemosyne summarise --status            Show coverage stats
"""

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------


def _get_cred(secret_name: str) -> str:
    result = subprocess.run(
        [
            "az",
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            "kv-tc-exp",
            "--name",
            secret_name,
            "--query",
            "value",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"Could not fetch secret '{secret_name}' from Key Vault")
    return value


# ---------------------------------------------------------------------------
# Vault doc discovery
# ---------------------------------------------------------------------------

_VAULT_ROOT = Path("/data/obsidian-vault")


def _discover_vault_docs() -> list[str]:
    """Return absolute paths for all .md files in the vault."""
    return [str(p) for p in _VAULT_ROOT.rglob("*.md") if p.is_file()]


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------


def _open_db() -> sqlite3.Connection:
    db_path = Path("/data/mnemosyne/summaries.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    from mnemosyne.summaries.staleness import init_summaries_db

    init_summaries_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_status(db: sqlite3.Connection) -> None:
    """Print coverage stats."""
    total_row = db.execute("SELECT COUNT(*) FROM summaries").fetchone()
    l0_row = db.execute("SELECT COUNT(*) FROM summaries WHERE l0 IS NOT NULL AND l0 != ''").fetchone()
    l1_row = db.execute("SELECT COUNT(*) FROM summaries WHERE l1 IS NOT NULL AND l1 != ''").fetchone()

    total = total_row[0] if total_row else 0
    l0 = l0_row[0] if l0_row else 0
    l1 = l1_row[0] if l1_row else 0

    vault_count = len(_discover_vault_docs())

    print(f"Vault docs:     {vault_count}")
    print(f"With L0:        {l0} / {total} stored")
    print(f"With L1:        {l1} / {total} stored")
    stale_count = max(0, vault_count - l0)
    print(f"Approx stale:   {stale_count}")


def _run_generate(
    paths: list[str],
    include_l1: bool,
    api_key: str,
    endpoint: str,
    deployment: str,
    db: sqlite3.Connection,
) -> None:
    """Generate summaries for paths and persist to DB."""
    from mnemosyne.summaries.generate import generate_summaries
    from mnemosyne.summaries.staleness import write_summary

    print(f"Generating summaries for {len(paths)} file(s) (include_l1={include_l1})...")
    results = generate_summaries(
        paths=paths,
        api_key=api_key,
        endpoint=endpoint,
        deployment=deployment,
        include_l1=include_l1,
        batch_size=10,
        sleep_ms=100,
    )

    for result in results:
        write_summary(result, db)

    print(f"Done: {len(results)} / {len(paths)} succeeded.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mnemosyne summarise",
        description="Generate L0/L1 tiered summaries for vault documents.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Generate for all vault docs")
    group.add_argument("--stale", action="store_true", help="Generate only stale/missing")
    group.add_argument("--path", metavar="FILE", help="Single file to summarise")
    group.add_argument("--status", action="store_true", help="Show coverage stats")

    parser.add_argument(
        "--include-l1",
        action="store_true",
        default=False,
        help="Also generate L1 structured overview (slower, more tokens)",
    )
    parser.add_argument(
        "--deployment",
        default="gpt-4o-mini",
        help="Azure OpenAI deployment name (default: gpt-4o-mini)",
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[2:])

    db = _open_db()

    if args.status:
        _cmd_status(db)
        db.close()
        return

    # Fetch credentials (only needed for generation)
    try:
        api_key = _get_cred("azure-openai-api-key")
        endpoint = _get_cred("azure-openai-endpoint")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.all:
        paths = _discover_vault_docs()
        if not paths:
            print("No vault docs found.", file=sys.stderr)
            sys.exit(1)
        _run_generate(paths, args.include_l1, api_key, endpoint, args.deployment, db)

    elif args.stale:
        all_paths = _discover_vault_docs()
        from mnemosyne.summaries.staleness import get_stale_paths

        paths = get_stale_paths(all_paths, db)
        print(f"Stale/missing: {len(paths)} of {len(all_paths)}")
        if not paths:
            print("Nothing to do.")
            db.close()
            return
        _run_generate(paths, args.include_l1, api_key, endpoint, args.deployment, db)

    elif args.path:
        p = Path(args.path)
        if not p.exists():
            print(f"File not found: {args.path}", file=sys.stderr)
            sys.exit(1)
        _run_generate([str(p)], args.include_l1, api_key, endpoint, args.deployment, db)

    db.close()


if __name__ == "__main__":
    main()
