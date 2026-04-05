#!/usr/bin/env python3
"""
build-eval-gold.py — Phase 5B: Gold label construction for v2-real-world eval suite

For each query (mined + curated):
  1. Run mnemosyne search --json --budget 5000 --agent {agent} → top-10 results
  2. Build candidate set: retrieved paths + candidate_gold_paths from mining
  3. LLM-judge each (query, candidate_path) pair 0/1/2 via mnemosyne._azure.chat_completion
  4. Paths with score ≥ 1 become gold
  5. Cases with zero gold paths: flagged needs_human_review=true, excluded from suite

Usage:
    sudo -u openclaw bash -c "source /opt/openclaw/env/openclaw.env && \\
      AZURE_OPENAI_ENDPOINT=$(az keyvault secret show ...) \\
      AZURE_OPENAI_API_KEY=$(az keyvault secret show ...) \\
      .venv/bin/python scripts/build-eval-gold.py [--curated scripts/curated-queries-input.yaml] \\
      [--max-candidates 15] [--dry-run]"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
VAULT_DIR   = Path("/data/obsidian-vault")
MNEMOSYNE   = Path("/opt/openclaw/bin/mnemosyne")

MINED_INPUT  = SCRIPT_DIR / "mined-queries-raw.jsonl"
SUITE_OUTPUT = PROJECT_DIR / "suites" / "v2-real-world.yaml"

# ── Config ────────────────────────────────────────────────────────────────────

MAX_CANDIDATES    = int(os.getenv("MAX_CANDIDATES", "15"))   # max candidates per query
SEARCH_BUDGET     = 5000
SEARCH_RESULT_K   = 10
MAX_CHUNK_CHARS   = 3000   # truncate vault content passed to LLM judge
JUDGE_RETRY       = 1      # retry count on parse failure
REQUEST_DELAY     = 0.4    # seconds between LLM calls (rate limit)

# ── Vendor import (lazy — only needed when not dry-run) ───────────────────────

def _load_azure():
    """Add qmd-azure-embed to sys.path and import mnemosyne._azure."""
    sys.path.insert(0, str(PROJECT_DIR))
    from mnemosyne import _azure  # type: ignore
    return _azure


# ── Search ────────────────────────────────────────────────────────────────────

def run_search(query: str, agent: str) -> tuple[list[str], dict]:
    """Run mnemosyne search, return (path_list, meta_dict)."""
    agent = agent or "shape"
    cmd = [
        str(MNEMOSYNE), "search", "--json",
        "--budget", str(SEARCH_BUDGET),
        "--agent", agent,
        query,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return [], {"error": result.stderr[:200]}
        data = json.loads(result.stdout)
        paths = [r["path"] for r in data.get("results", [])[:SEARCH_RESULT_K]]
        meta = {
            "intent":     data.get("intent"),
            "bm25_count": data.get("bm25_count"),
            "vec_count":  data.get("vec_count"),
            "vec_failed": data.get("vec_failed", False),
            "latency_ms": data.get("latency_ms"),
        }
        return paths, meta
    except subprocess.TimeoutExpired:
        return [], {"error": "timeout"}
    except (json.JSONDecodeError, KeyError) as e:
        return [], {"error": str(e)}


# ── Vault content reader ──────────────────────────────────────────────────────

# QMD collection-relative base directories.
# Search returns paths relative to each collection's root, not the vault root.
# Try each base dir until the path resolves.
_VAULT_BASE_DIRS = [
    VAULT_DIR,                                                  # vault root (catch-all)
    VAULT_DIR / "04-Agent-Knowledge",                           # vault-agent-knowledge
    VAULT_DIR / "04-Agent-Knowledge" / "shared",                # knowledge-shared
    VAULT_DIR / "04-Agent-Knowledge" / "entities",              # vault-entities
    VAULT_DIR / "02-Areas",                                     # vault-areas
    VAULT_DIR / "01-Projects",                                  # vault-projects
    VAULT_DIR / "03-Resources",                                 # vault-resources
    VAULT_DIR / "06-Archive",                                   # vault-archive
]


def _resolve_path_case_insensitive(base: Path, rel: str) -> Path | None:
    """Resolve base/rel with case-insensitive matching at each path level."""
    parts = Path(rel).parts
    candidate = base
    for part in parts:
        try:
            matches = [p for p in candidate.iterdir() if p.name.lower() == part.lower()]
        except PermissionError:
            return None
        if not matches:
            return None
        candidate = matches[0]
    return candidate


def read_vault_chunk(path: str) -> str:
    """Read vault file, return first MAX_CHUNK_CHARS characters.

    Tries multiple base directories because QMD search returns collection-relative
    paths (e.g. "shared/rules.md" from knowledge-shared, not
    "04-Agent-Knowledge/shared/rules.md").
    """
    for base in _VAULT_BASE_DIRS:
        resolved = _resolve_path_case_insensitive(base, path)
        if resolved and resolved.is_file():
            try:
                return resolved.read_text(errors="replace")[:MAX_CHUNK_CHARS]
            except OSError:
                continue
    return ""


# ── LLM judge ────────────────────────────────────────────────────────────────

def llm_judge(azure, query: str, path: str, chunk: str) -> int:
    """Score (query, path) relevance 0/1/2 via LLM. Returns int."""
    if not chunk.strip():
        return 0

    prompt = (
        f"Rate the relevance of this document to the search query on a scale of 0-2:\n"
        f"2 = highly relevant (directly answers the query)\n"
        f"1 = partially relevant (related context, useful but incomplete)\n"
        f"0 = not relevant\n\n"
        f"Search query: {query}\n\n"
        f"Document path: {path}\n"
        f"Document content (truncated):\n{chunk}\n\n"
        f"Reply with only the integer (0, 1, or 2)."
    )

    messages = [{"role": "user", "content": prompt}]

    for attempt in range(JUDGE_RETRY + 1):
        try:
            response = azure.chat_completion(messages, max_tokens=5)
            score_str = response.strip()
            score = int(score_str)
            if score in (0, 1, 2):
                return score
            # Out of range — clamp
            return max(0, min(2, score))
        except (ValueError, AttributeError):
            if attempt < JUDGE_RETRY:
                time.sleep(0.5)
                continue
            return 0
        except Exception:
            if attempt < JUDGE_RETRY:
                time.sleep(1.0)
                continue
            return 0
    return 0


# ── Curated query loader ──────────────────────────────────────────────────────

def load_curated(path: Path) -> list[dict]:
    """Load curated queries from YAML file."""
    if not path.exists():
        print(f"[WARN] Curated file not found: {path}", file=sys.stderr)
        return []
    with path.open() as f:
        data = yaml.safe_load(f)
    queries = data.get("queries", []) if isinstance(data, dict) else data
    print(f"[INFO] Loaded {len(queries)} curated queries from {path}")
    return queries


# ── NDCG ideal DCG helper (not used here but validated in runner) ─────────────

def ideal_dcg(n_gold: int, k: int = 10) -> float:
    """Ideal DCG for a case with n_gold perfect (score=2) results."""
    import math
    return sum(2.0 / math.log2(i + 2) for i in range(min(n_gold, k)))


# ── Main gold-build loop ──────────────────────────────────────────────────────

def build_gold(
    mined_path: Path,
    curated_path: Path | None,
    max_candidates: int,
    dry_run: bool,
) -> None:
    if not dry_run:
        azure = _load_azure()

    # ── Load queries ──────────────────────────────────────────────────────────

    mined_entries: list[dict] = []
    if mined_path.exists():
        with mined_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    mined_entries.append(json.loads(line))
    print(f"[INFO] Mined queries: {len(mined_entries)}")

    curated_entries: list[dict] = []
    if curated_path:
        curated_entries = load_curated(curated_path)
    print(f"[INFO] Curated queries: {len(curated_entries)}")

    all_entries: list[dict] = []

    # Normalise mined entries
    for e in mined_entries:
        all_entries.append({
            "id":                   e.get("id", ""),
            "query":                e.get("query", ""),
            "agent":                e.get("agent", "shape"),
            "category":             e.get("intent_from_log") or "semantic",
            "candidate_gold_paths": e.get("candidate_gold_paths", []),
            "source":               "session_log",
            "session_ids":          e.get("session_ids", []),
            "frequency":            e.get("frequency", 1),
            "has_tool_result":      e.get("has_tool_result", False),
            "notes":                f"Mined from session {', '.join(e.get('session_ids', [])[:1])}",
        })

    # Normalise curated entries
    curated_id_counter = 1
    for e in curated_entries:
        all_entries.append({
            "id":                   e.get("id") or f"C{curated_id_counter:03d}",
            "query":                e.get("query", ""),
            "agent":                e.get("agent", "shape"),
            "category":             e.get("category", "semantic"),
            "candidate_gold_paths": e.get("candidate_gold_paths", []),
            "source":               "curated",
            "session_ids":          [],
            "frequency":            1,
            "has_tool_result":      False,
            "notes":                e.get("notes", ""),
        })
        curated_id_counter += 1

    total = len(all_entries)
    print(f"[INFO] Total queries to process: {total}")

    if dry_run:
        print("\n[DRY RUN] Would process the following queries:")
        for i, e in enumerate(all_entries[:20]):
            print(f"  {e['id']:>6}  [{e['category']:<10}] [{e['source']:<11}] {e['query'][:70]}")
        if total > 20:
            print(f"  ... and {total - 20} more")
        print(f"\n[DRY RUN] Estimated LLM calls: ~{total * 7} (avg 7 candidates/query)")
        print(f"[DRY RUN] Estimated cost: ~${total * 7 * 0.0001:.2f} (very rough)")
        print(f"\n[DRY RUN] Output would be: {SUITE_OUTPUT}")
        return

    # ── Process each query ────────────────────────────────────────────────────

    cases: list[dict] = []
    skipped_no_gold: list[str] = []
    error_count    = 0
    total_llm_calls = 0

    for i, entry in enumerate(all_entries):
        qid      = entry["id"]
        query    = entry["query"]
        agent    = entry["agent"]
        category = entry["category"]
        source   = entry["source"]

        if not query or len(query) < 10:
            print(f"  [{i+1:>4}/{total}] {qid:<8} SKIP (short query)")
            continue

        print(f"  [{i+1:>4}/{total}] {qid:<8} [{category:<10}] {query[:55]}")

        # 1. Run search
        retrieved_paths, search_meta = run_search(query, agent)
        if search_meta.get("vec_failed"):
            print(f"             ↳ vec_failed — using BM25 only")

        # 2. Build candidate set
        candidate_set: list[str] = []
        seen = set()
        for p in retrieved_paths:
            if p and p not in seen:
                candidate_set.append(p)
                seen.add(p)
        for p in entry.get("candidate_gold_paths", []):
            if p and p not in seen:
                candidate_set.append(p)
                seen.add(p)
        candidate_set = candidate_set[:max_candidates]

        if not candidate_set:
            print(f"             ↳ no candidates — flagging needs_human_review")
            skipped_no_gold.append(qid)
            continue

        # 3. LLM judge each candidate
        gold_paths: list[dict] = []
        for path in candidate_set:
            chunk = read_vault_chunk(path)
            score = llm_judge(azure, query, path, chunk)
            total_llm_calls += 1
            time.sleep(REQUEST_DELAY)
            if score >= 1:
                gold_paths.append({"path": path, "relevance": score})

        gold_paths.sort(key=lambda x: -x["relevance"])

        if not gold_paths:
            print(f"             ↳ 0 gold paths — flagging needs_human_review")
            skipped_no_gold.append(qid)
            continue

        judge_confidence = "high" if any(g["relevance"] == 2 for g in gold_paths) else "low"
        print(f"             ↳ {len(gold_paths)} gold paths, confidence={judge_confidence}")

        cases.append({
            "id":               f"R-{qid}",
            "category":         category,
            "query":            query,
            "gold_paths":       gold_paths,
            "score_method":     "ndcg",
            "agent":            agent,
            "source":           source,
            "judge_confidence": judge_confidence,
            "has_tool_result":  entry.get("has_tool_result", False),
            "notes":            entry.get("notes", ""),
            "search_intent":    search_meta.get("intent"),
            "search_latency_ms": search_meta.get("latency_ms"),
        })

    # ── Report ────────────────────────────────────────────────────────────────

    print(f"\n[DONE] Total LLM calls: {total_llm_calls}")
    print(f"[DONE] Gold cases: {len(cases)}")
    print(f"[DONE] Excluded (no gold): {len(skipped_no_gold)}")

    # Category distribution
    from collections import Counter
    cat_dist = Counter(c["category"] for c in cases)
    src_dist = Counter(c["source"] for c in cases)
    print(f"[DONE] By category: {dict(cat_dist)}")
    print(f"[DONE] By source:   {dict(src_dist)}")

    thin_cats = [cat for cat, count in cat_dist.items() if count < 5]
    if thin_cats:
        print(f"[WARN] Thin categories (<5 cases): {thin_cats}")

    # ── Write suite YAML ──────────────────────────────────────────────────────

    import datetime
    suite = {
        "meta": {
            "name":       "v2-real-world",
            "version":    "1.0",
            "instrument": "mnemosyne-hybrid-phase5",
            "built":      datetime.date.today().isoformat(),
            "n_cases":    len(cases),
            "n_excluded_no_gold": len(skipped_no_gold),
            "category_distribution": dict(cat_dist),
            "source_distribution":   dict(src_dist),
        },
        "excluded_no_gold": skipped_no_gold,
        "cases": cases,
    }

    SUITE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    class _IndentedDumper(yaml.Dumper):
        pass

    def _str_representer(dumper, data):
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    _IndentedDumper.add_representer(str, _str_representer)

    with SUITE_OUTPUT.open("w") as f:
        yaml.dump(suite, f, Dumper=_IndentedDumper, allow_unicode=True,
                  default_flow_style=False, indent=2, sort_keys=False)

    print(f"[DONE] Written: {SUITE_OUTPUT}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5B: Build eval gold labels")
    parser.add_argument(
        "--curated",
        type=Path,
        default=SCRIPT_DIR / "curated-queries-input.yaml",
        help="Path to curated queries YAML (default: scripts/curated-queries-input.yaml)",
    )
    parser.add_argument(
        "--max-candidates", type=int, default=MAX_CANDIDATES,
        help=f"Max candidate paths per query (default: {MAX_CANDIDATES})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print query list and estimated cost without running LLM judge",
    )
    parser.add_argument(
        "--mined", type=Path, default=MINED_INPUT,
        help=f"Mined queries JSONL (default: {MINED_INPUT})",
    )
    parser.add_argument(
        "--skip-mined", action="store_true",
        help="Only process curated queries — skip mined JSONL entirely (faster re-runs)",
    )
    args = parser.parse_args()

    mined_path = Path("/dev/null") if args.skip_mined else args.mined
    curated_path = args.curated if args.curated.exists() else None
    if not curated_path:
        print(f"[INFO] No curated queries file at {args.curated} — using mined only")

    build_gold(
        mined_path=mined_path,
        curated_path=curated_path,
        max_candidates=args.max_candidates,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
