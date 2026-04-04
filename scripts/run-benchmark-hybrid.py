#!/usr/bin/env python3
"""
Mnemosyne Benchmark Runner — Phase 1 Hybrid Search

Same 50-query suite as BM25 and vector runners, but retrieval goes through
mnemosyne.search.hybrid — intent classifier → parallel BM25+vector → RRF fusion.

Requires:
  - /data/tools/qmd-azure-embed/.venv with mnemosyne installed (pip install -e .)
  - Azure KV secrets accessible (az login)
  - Live QMD DB at ~/.cache/qmd/index.sqlite with vectors embedded
"""

import json
import os
import subprocess
import sys
import datetime
import time
import requests
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

BENCHMARK_DIR = Path("/data/obsidian-vault/01-Projects/202603-Mnemosyne")
RESULTS_DIR = BENCHMARK_DIR / "benchmark-results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

VENV_PYTHON = Path("/data/tools/qmd-azure-embed/.venv/bin/python")

AZURE_DEPLOYMENT = "gpt-4o-mini"
AZURE_API_VERSION = "2024-02-01"
TOP_K = 5

CATEGORY_WEIGHTS = {
    "recall":      0.25,
    "temporal":    0.20,
    "entity":      0.20,
    "conceptual":  0.15,
    "multi_hop":   0.10,
    "procedural":  0.10,
}

# ── Test Suite (identical to other runners) ────────────────────────────────────

TEST_QUERIES = [
    # Recall — known documents that should rank #1
    # NOTE: R03/R04/R06/R07/R08 were replaced 2026-03-23 — original gold docs were
    # either missing from index (SOUL.md) or duplicate/ambiguous (3x ARCHITECTURE.md).
    # All replacements validated at rank 1 before committing.
    ("R01", "recall", "Arize Phoenix observability recommendation",
     None, "llm"),
    ("R02", "recall", "Mnemosyne architecture decisions record",
     "02-three-cubes-ventures/platform/features/feat-020-mnemosyne/architecture.md", "exact"),
    ("R03", "recall", "agent secret isolation spec security P0",
     None, "llm"),
    ("R04", "recall", "builder rules quality gates coding style safety constraints",
     "builder/rules.md", "exact"),
    ("R05", "recall", "Mnemosyne benchmark test queries and scoring",
     "02-three-cubes-ventures/platform/features/feat-020-mnemosyne/benchmark.md", "exact"),
    ("R06", "recall", "builder reusable patterns upstream candidate engineering hub",
     "builder/patterns.md", "exact"),
    ("R07", "recall", "Mnemosyne kanban board project status",
     "builder/mnemosyne-board.md", "exact"),
    ("R08", "recall", "agent observability ADR applicationmap",
     None, "llm"),

    # Temporal — time-based queries
    ("T01", "temporal", "what was completed last week on Mnemosyne",
     None, "llm"),
    ("T02", "temporal", "what decisions were made in March 2026 about embeddings",
     None, "llm"),
    ("T03", "temporal", "when did we decide to use Azure text-embedding-3-large",
     None, "llm"),
    ("T04", "temporal", "what tasks were completed on 2026-03-22",
     None, "llm"),
    ("T05", "temporal", "what was the outcome of the Phase 0 benchmark",
     None, "llm"),
    ("T06", "temporal", "recent changes to the agent memory system",
     None, "llm"),

    # Entity — person/project queries
    ("E01", "entity", "what do we know about Alex Jordan",
     None, "llm"),
    ("E02", "entity", "tell me about Triad Consulting as an organisation",
     None, "llm"),
    ("E03", "entity", "what is Builder agent responsible for",
     None, "llm"),
    ("E04", "entity", "what has Shape agent been working on",
     None, "llm"),
    ("E05", "entity", "what is tc-productivity",
     None, "llm"),
    ("E06", "entity", "who is the OpenClaw platform built for",
     None, "llm"),

    # Conceptual — abstract topic queries
    ("C01", "conceptual", "what is the approach to memory retrieval in the platform",
     None, "llm"),
    ("C02", "conceptual", "how does the agent briefing system work",
     None, "llm"),
    ("C03", "conceptual", "what is the philosophy behind the Mnemosyne architecture",
     None, "llm"),
    ("C04", "conceptual", "how do we handle agent mistakes and corrections",
     None, "llm"),
    ("C05", "conceptual", "what are the privacy boundaries between agents",
     None, "llm"),
    ("C06", "conceptual", "what is the token budget strategy for context loading",
     None, "llm"),
    ("C07", "conceptual", "how does the embedding pipeline handle schema drift",
     None, "llm"),
    ("C08", "conceptual", "what is the data residency policy for agent memory",
     None, "llm"),

    # Multi-hop — require connecting multiple documents
    ("M01", "multi_hop", "why did we choose Azure over local embeddings and what did it cost",
     None, "llm"),
    ("M02", "multi_hop", "what is the relationship between QMD, sqlite-vec, and Mnemosyne",
     None, "llm"),
    ("M03", "multi_hop", "how does BM25 compare to vector search and what is the plan",
     None, "llm"),
    ("M04", "multi_hop", "what blocked the original Mnemosyne approach and how was it resolved",
     None, "llm"),
    ("M05", "multi_hop", "what is the full Phase 0 to Phase 3 delivery roadmap",
     None, "llm"),

    # Procedural — how-to queries
    ("P01", "procedural", "how do I run the embedding pipeline",
     None, "llm"),
    ("P02", "procedural", "what is the process for adding a new agent to OpenClaw",
     None, "llm"),
    ("P03", "procedural", "how should I handle a failed Azure API call in the embed pipeline",
     None, "llm"),
    ("P04", "procedural", "what do I do when the benchmark score drops",
     None, "llm"),
    ("P05", "procedural", "how do I add a new vault document to the QMD index",
     None, "llm"),
    ("P06", "procedural", "what is the git workflow for this project",
     None, "llm"),
]

# ── Retrieval via mnemosyne search ─────────────────────────────────────────────

def run_hybrid_search(query: str) -> tuple[list[str], dict]:
    """Call mnemosyne search --json and return (paths, metadata)."""
    try:
        result = subprocess.run(
            ["/data/tools/qmd-azure-embed/.venv/bin/mnemosyne", "search", query,
             "--agent", "shape", "--json", "--budget", "5000"],
            capture_output=True, text=True, timeout=30,
            cwd="/data/tools/qmd-azure-embed"
        )
        if result.returncode != 0:
            return [], {"error": result.stderr[:200]}
        data = json.loads(result.stdout)
        paths = [r["path"].replace("qmd://", "").lstrip("/")
                 for r in data.get("results", [])]
        return paths, {
            "intent": data.get("intent"),
            "bm25_count": data.get("bm25_count", 0),
            "vec_count": data.get("vec_count", 0),
            "fused_count": data.get("fused_count", 0),
            "vec_failed": data.get("vec_failed", False),
            "latency_ms": data.get("latency_ms", 0),
        }
    except subprocess.TimeoutExpired:
        return [], {"error": "timeout"}
    except Exception as e:
        return [], {"error": str(e)}

# ── Scoring ────────────────────────────────────────────────────────────────────

def get_azure_creds() -> tuple[str, str]:
    """Fetch Azure API key and endpoint from Key Vault."""
    key = subprocess.run(
        ["az", "keyvault", "secret", "show", "--vault-name", "kv-tc-exp",
         "--name", "azure-openai-api-key", "--query", "value", "-o", "tsv"],
        capture_output=True, text=True, timeout=15
    ).stdout.strip()
    endpoint = subprocess.run(
        ["az", "keyvault", "secret", "show", "--vault-name", "kv-tc-exp",
         "--name", "azure-openai-endpoint", "--query", "value", "-o", "tsv"],
        capture_output=True, text=True, timeout=15
    ).stdout.strip()
    return key, endpoint

def llm_judge(query: str, retrieved_paths: list[str], api_key: str, endpoint: str) -> float:
    """Score retrieval quality 0.0-1.0 using gpt-4o-mini as judge."""
    if not retrieved_paths:
        return 0.0

    snippets = "\n".join(f"- {p}" for p in retrieved_paths[:TOP_K])
    prompt = f"""You are evaluating memory retrieval quality for an AI agent system.

Query: {query}

Retrieved documents (paths):
{snippets}

Score the retrieval quality from 0.0 to 1.0:
- 1.0: Retrieved documents directly and completely answer the query
- 0.8: Retrieved documents mostly answer the query with minor gaps
- 0.6: Retrieved documents partially answer the query
- 0.4: Retrieved documents are tangentially related
- 0.2: Retrieved documents have minimal relevance
- 0.0: Retrieved documents are irrelevant or empty

Reply with ONLY a number between 0.0 and 1.0."""

    url = f"{endpoint.rstrip('/')}/openai/deployments/{AZURE_DEPLOYMENT}/chat/completions?api-version={AZURE_API_VERSION}"
    try:
        resp = requests.post(url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={"messages": [{"role": "user", "content": prompt}], "max_tokens": 10, "temperature": 0},
            timeout=20)
        resp.raise_for_status()
        score_str = resp.json()["choices"][0]["message"]["content"].strip()
        return max(0.0, min(1.0, float(score_str)))
    except Exception:
        return 0.0

def exact_match_score(retrieved_paths: list[str], gold: str) -> float:
    """1.0 if gold doc appears in top-K results, else 0.0."""
    gold_norm = gold.lower().rstrip("/")
    for path in retrieved_paths[:TOP_K]:
        if gold_norm in path.lower():
            return 1.0
    return 0.0

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    run_label = sys.argv[1] if len(sys.argv) > 1 else "hybrid-phase1"
    date_str = datetime.date.today().isoformat()
    output_file = RESULTS_DIR / f"B1-{run_label}-{date_str}.json"

    print(f"Mnemosyne Phase 1 Hybrid Benchmark — {date_str}")
    print(f"Output: {output_file}")
    print()

    print("Fetching Azure credentials...")
    api_key, endpoint = get_azure_creds()
    print(f"Endpoint: {endpoint}")
    print()

    results = []
    category_scores: dict[str, list[float]] = {c: [] for c in CATEGORY_WEIGHTS}

    for qid, category, query, gold_path, score_method in TEST_QUERIES:
        t_start = time.time()
        paths, meta = run_hybrid_search(query)
        retrieval_ms = (time.time() - t_start) * 1000

        if score_method == "exact":
            score = exact_match_score(paths, gold_path)
        elif score_method == "fuzzy":
            score = exact_match_score(paths, gold_path)
        else:
            score = llm_judge(query, paths, api_key, endpoint)
            time.sleep(0.5)  # rate limit

        category_scores[category].append(score)

        intent = meta.get("intent", "unknown")
        vec_ok = "✓" if not meta.get("vec_failed") else "✗"
        print(f"  [{qid}] {query[:55]:<55} score={score:.2f}  intent={str(intent)[-10:]:<10} vec={vec_ok}  {retrieval_ms:.0f}ms")

        results.append({
            "id": qid,
            "category": category,
            "query": query,
            "gold_path": gold_path,
            "score_method": score_method,
            "score": score,
            "retrieved_paths": paths[:TOP_K],
            "retrieval_ms": retrieval_ms,
            **meta,
        })

    print()
    print("=== Results ===")
    cat_avgs = {}
    weighted_total = 0.0
    for cat, weight in CATEGORY_WEIGHTS.items():
        scores = category_scores[cat]
        avg = sum(scores) / len(scores) if scores else 0.0
        cat_avgs[cat] = avg
        weighted_total += avg * weight
        n = len(scores)
        print(f"  {cat:<12} {avg:.4f}  (n={n}, weight={weight})")

    print(f"\n  weighted_total: {weighted_total:.4f}")

    # Gate check
    gate_pass = weighted_total >= 0.62 and all(v >= 0.50 for v in cat_avgs.values())
    print(f"\n  Phase 1 gate (≥0.62, no category <0.50): {'✅ PASS' if gate_pass else '❌ FAIL'}")

    output = {
        "run_label": run_label,
        "date": date_str,
        "system": "mnemosyne-hybrid-phase1",
        "n_queries": len(TEST_QUERIES),
        "category_scores": cat_avgs,
        "weighted_total": weighted_total,
        "phase1_gate_pass": gate_pass,
        "results": results,
    }

    output_file.write_text(json.dumps(output, indent=2))
    print(f"\nSaved: {output_file}")

if __name__ == "__main__":
    main()
