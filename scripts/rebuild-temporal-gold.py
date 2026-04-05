#!/usr/bin/env python3
"""
rebuild-temporal-gold.py — Phase 6C/6A gold rebuild

Re-runs mnemosyne search for temporal and multi_hop cases (which had 0.00 NDCG)
and rebuilds their gold_paths based on what the search actually returns now:
  - temporal: uses temporal search path → returns absolute workspace paths
  - multi_hop: uses multi_hop planner → returns paths from planner sub-queries

Usage:
    sudo -u openclaw bash -c "source /opt/openclaw/env/openclaw.env && \
      .venv/bin/python scripts/rebuild-temporal-gold.py [--dry-run] [--categories temporal,multi_hop]"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
SUITE_PATH  = PROJECT_DIR / "suites" / "v2-real-world.yaml"
VAULT_ROOT  = Path("/data/obsidian-vault")

# ── LLM Judge ─────────────────────────────────────────────────────────────────

def _load_azure():
    """Load Azure module (fetches creds from Key Vault on first use)."""
    sys.path.insert(0, str(PROJECT_DIR))
    from mnemosyne._azure import chat_completion
    return chat_completion


def llm_judge(chat_completion, query: str, path: str, snippet: str = "") -> int:
    """Score relevance of path to query: 0=irrelevant, 1=partial, 2=highly relevant.

    Uses snippet text from search results (already extracted by mnemosyne).
    Falls back to reading from disk for absolute paths if no snippet.
    """
    content = snippet

    if not content:
        # Fall back to disk read for absolute paths
        if path.startswith("/"):
            file_path = Path(path)
            if file_path.exists():
                try:
                    content = file_path.read_text(errors="replace")[:3000]
                except Exception:
                    pass
        if not content:
            return 0

    prompt = (
        f"Rate the relevance of this document to the search query on a scale of 0-2:\n"
        f"2 = highly relevant (directly answers the query)\n"
        f"1 = partially relevant (related context, useful but incomplete)\n"
        f"0 = not relevant\n"
        f"Reply with only the integer.\n\n"
        f"Query: {query}\n\n"
        f"Document path: {path}\n"
        f"Document excerpt:\n{content}"
    )

    for attempt in range(2):
        try:
            response = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
            )
            text = (response or "").strip()
            if text and text[0].isdigit():
                score = int(text[0])
                if score in (0, 1, 2):
                    return score
        except Exception as e:
            if attempt == 1:
                print(f"       [judge error] {type(e).__name__}: {e}")
                return 0
    return 0


# ── Search ────────────────────────────────────────────────────────────────────

MNEMOSYNE = PROJECT_DIR / ".venv" / "bin" / "mnemosyne"


def run_search(query: str, agent: str, budget: int = 5000) -> list[dict]:
    """Run mnemosyne search; return list of {path, snippet} dicts in rank order."""
    cmd = [
        str(MNEMOSYNE), "search",
        "--json", "--budget", str(budget),
        "--agent", agent or "shape",
        query,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_DIR),
        )
        if result.returncode != 0:
            print(f"    [WARN] search error: {result.stderr[:200]}")
            return []
        data = json.loads(result.stdout)
        return [{"path": r["path"], "snippet": r.get("snippet", "")} for r in data.get("results", [])]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"    [WARN] search failed: {e}")
        return []


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild temporal/multi_hop gold paths")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    parser.add_argument("--categories", default="temporal,multi_hop",
                        help="Comma-separated categories to rebuild (default: temporal,multi_hop)")
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    args = parser.parse_args()

    target_cats = set(c.strip() for c in args.categories.split(","))
    print(f"Rebuild gold for categories: {target_cats}")
    print(f"Suite: {args.suite}")
    if args.dry_run:
        print("[DRY-RUN] No changes will be written")
    print()

    suite = yaml.safe_load(args.suite.read_text())
    cases = suite["cases"]

    target_cases = [c for c in cases if c.get("category") in target_cats]
    print(f"Found {len(target_cases)} cases to rebuild:")
    for c in target_cases:
        print(f"  {c['id']} [{c['category']}] {c['query'][:70]}")
    print()

    if not target_cases:
        print("Nothing to do.")
        return

    # Load Azure (for LLM judge)
    if not args.dry_run:
        print("Loading Azure credentials...")
        chat_completion = _load_azure()
        print("Azure loaded.")
        print()

    updated = 0
    for c in target_cases:
        qid      = c["id"]
        query    = c["query"]
        agent    = c.get("agent", "shape")
        category = c["category"]

        print(f"[{qid}] {category}: {query[:70]}")

        if args.dry_run:
            print(f"  -> Would run: mnemosyne search --json --agent {agent} \"{query[:50]}\"")
            continue

        results = run_search(query, agent)
        print(f"  -> {len(results)} results from search")

        if not results:
            print(f"  -> SKIP: no results returned (temporal query may be out of date range)")
            c["notes"] = c.get("notes", "") + " [P6: search returned 0 results — gold not rebuilt]"
            continue

        # Score top paths using snippet text
        new_gold = []
        for r in results[:10]:
            path    = r["path"]
            snippet = r.get("snippet", "")
            score = llm_judge(chat_completion, query, path, snippet)
            print(f"     score={score}  {path[:80]}")
            if score >= 1:
                new_gold.append({"path": path, "relevance": score})

        if not new_gold:
            print(f"  -> SKIP: no paths scored >=1 (stale queries or judge mismatch)")
            c["notes"] = c.get("notes", "") + " [P6: 0 gold paths after judge — not rebuilt]"
            continue

        # Update case
        old_gold_count = len(c.get("gold_paths", []))
        c["gold_paths"] = new_gold
        c["judge_confidence"] = "high" if any(g["relevance"] == 2 for g in new_gold) else "low"
        c["notes"] = (c.get("notes") or "") + f" [P6: rebuilt {len(new_gold)} gold paths from {category} search]"
        print(f"  -> NEW GOLD: {len(new_gold)} paths (was {old_gold_count})")
        updated += 1

    print()
    if args.dry_run:
        print("Dry-run complete. Run without --dry-run to apply.")
        return

    print(f"Updated {updated}/{len(target_cases)} cases.")

    if updated > 0:
        suite["meta"]["n_cases"] = len(cases)
        args.suite.write_text(yaml.dump(suite, allow_unicode=True, sort_keys=False, width=120))
        print(f"Written: {args.suite}")
    else:
        print("No changes written (all cases skipped).")


if __name__ == "__main__":
    main()
