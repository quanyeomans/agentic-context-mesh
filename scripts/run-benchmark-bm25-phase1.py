#!/usr/bin/env python3
"""
Mnemosyne Benchmark Runner — BM25 only, Phase 1 query set

Runs the same 39-query suite as the Phase 1 hybrid benchmark,
but retrieval uses plain QMD BM25 search (qmd search --json).

Used to establish a fair before/after comparison:
  B0: BM25 only (original Phase 0 suite — different queries)
  B1-bm25: BM25 only on Phase 1 suite (this script)
  B1-hybrid: Hybrid on Phase 1 suite (run-benchmark-hybrid.py)

Run:
  cd /tmp/qmd-azure-embed
  .venv/bin/python /data/.../run-benchmark-bm25-phase1.py bm25-phase1-baseline
"""

import json
import subprocess
import sys
import datetime
import time
import requests
from pathlib import Path

BENCHMARK_DIR = Path("/data/obsidian-vault/01-Projects/202603-Mnemosyne")
RESULTS_DIR = BENCHMARK_DIR / "benchmark-results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

QMD = "/data/workspace/.tools/qmd/node_modules/.bin/qmd"

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

# ── Same query set as run-benchmark-hybrid.py (Phase 1 fixed suite) ────────────

TEST_QUERIES = [
    # Recall
    ("R01", "recall", "Arize Phoenix observability recommendation",
     "01-projects/202603-arize-observability-research/research-report.md", "exact"),
    ("R02", "recall", "Mnemosyne architecture decisions record",
     "01-projects/202603-mnemosyne/architecture.md", "exact"),
    ("R03", "recall", "agent secret isolation spec security P0",
     "01-projects/202603-agent-secret-isolation/spec.md", "exact"),
    ("R04", "recall", "builder rules quality gates coding style safety constraints",
     "04-agent-knowledge/builder/rules.md", "exact"),
    ("R05", "recall", "Mnemosyne benchmark test queries and scoring",
     "01-projects/202603-mnemosyne/benchmark.md", "exact"),
    ("R06", "recall", "builder reusable patterns upstream candidate engineering hub",
     "04-agent-knowledge/builder/patterns.md", "exact"),
    ("R07", "recall", "Mnemosyne kanban board project status",
     "01-projects/boards/mnemosyne.md", "exact"),
    ("R08", "recall", "agent observability ADR applicationmap",
     "01-projects/202603-agent-observability/adr-applicationmap.md", "exact"),
    # Temporal
    ("T01", "temporal", "what was completed last week on Mnemosyne", None, "llm"),
    ("T02", "temporal", "what decisions were made in March 2026 about embeddings", None, "llm"),
    ("T03", "temporal", "when did we decide to use Azure text-embedding-3-large", None, "llm"),
    ("T04", "temporal", "what tasks were completed on 2026-03-22", None, "llm"),
    ("T05", "temporal", "what was the outcome of the Phase 0 benchmark", None, "llm"),
    ("T06", "temporal", "recent changes to the agent memory system", None, "llm"),
    # Entity
    ("E01", "entity", "what do we know about Alex Jordan", None, "llm"),
    ("E02", "entity", "tell me about Triad Consulting as an organisation", None, "llm"),
    ("E03", "entity", "what is Builder agent responsible for", None, "llm"),
    ("E04", "entity", "what has Shape agent been working on", None, "llm"),
    ("E05", "entity", "what is tc-productivity", None, "llm"),
    ("E06", "entity", "who is the OpenClaw platform built for", None, "llm"),
    # Conceptual
    ("C01", "conceptual", "what is the approach to memory retrieval in the platform", None, "llm"),
    ("C02", "conceptual", "how does the agent briefing system work", None, "llm"),
    ("C03", "conceptual", "what is the philosophy behind the Mnemosyne architecture", None, "llm"),
    ("C04", "conceptual", "how do we handle agent mistakes and corrections", None, "llm"),
    ("C05", "conceptual", "what are the privacy boundaries between agents", None, "llm"),
    ("C06", "conceptual", "what is the token budget strategy for context loading", None, "llm"),
    ("C07", "conceptual", "how does the embedding pipeline handle schema drift", None, "llm"),
    ("C08", "conceptual", "what is the data residency policy for agent memory", None, "llm"),
    # Multi-hop
    ("M01", "multi_hop", "why did we choose Azure over local embeddings and what did it cost", None, "llm"),
    ("M02", "multi_hop", "what is the relationship between QMD, sqlite-vec, and Mnemosyne", None, "llm"),
    ("M03", "multi_hop", "how does BM25 compare to vector search and what is the plan", None, "llm"),
    ("M04", "multi_hop", "what blocked the original Mnemosyne approach and how was it resolved", None, "llm"),
    ("M05", "multi_hop", "what is the full Phase 0 to Phase 3 delivery roadmap", None, "llm"),
    # Procedural
    ("P01", "procedural", "how do I run the embedding pipeline", None, "llm"),
    ("P02", "procedural", "what is the process for adding a new agent to OpenClaw", None, "llm"),
    ("P03", "procedural", "how should I handle a failed Azure API call in the embed pipeline", None, "llm"),
    ("P04", "procedural", "what do I do when the benchmark score drops", None, "llm"),
    ("P05", "procedural", "how do I add a new vault document to the QMD index", None, "llm"),
    ("P06", "procedural", "what is the git workflow for this project", None, "llm"),
]

# ── Retrieval ──────────────────────────────────────────────────────────────────

def run_bm25(query: str) -> tuple[list[str], float]:
    t = time.time()
    try:
        result = subprocess.run(
            [QMD, "search", query, "--json", "--limit", str(TOP_K)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return [], (time.time() - t) * 1000
        data = json.loads(result.stdout)
        paths = [r.get("file", r.get("path", "")).replace("qmd://", "").split("/", 1)[-1] for r in data[:TOP_K]]
        return paths, (time.time() - t) * 1000
    except Exception:
        return [], (time.time() - t) * 1000

# ── Scoring ────────────────────────────────────────────────────────────────────

def get_azure_creds() -> tuple[str, str]:
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
        return max(0.0, min(1.0, float(resp.json()["choices"][0]["message"]["content"].strip())))
    except Exception:
        return 0.0

def exact_match_score(retrieved_paths: list[str], gold: str) -> float:
    gold_norm = gold.lower().rstrip("/")
    for path in retrieved_paths[:TOP_K]:
        if gold_norm in path.lower():
            return 1.0
    return 0.0

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    run_label = sys.argv[1] if len(sys.argv) > 1 else "bm25-phase1-baseline"
    date_str = datetime.date.today().isoformat()
    output_file = RESULTS_DIR / f"B1-{run_label}-{date_str}.json"

    print(f"Mnemosyne Phase 1 BM25 Baseline Benchmark — {date_str}")
    print(f"System: QMD BM25 only (qmd search)")
    print(f"Output: {output_file}")
    print()

    print("Fetching Azure credentials...")
    api_key, endpoint = get_azure_creds()
    print()

    results = []
    category_scores: dict[str, list[float]] = {c: [] for c in CATEGORY_WEIGHTS}

    for qid, category, query, gold_path, score_method in TEST_QUERIES:
        paths, latency_ms = run_bm25(query)

        if score_method in ("exact", "fuzzy"):
            score = exact_match_score(paths, gold_path or "")
        else:
            score = llm_judge(query, paths, api_key, endpoint)
            time.sleep(0.3)

        category_scores[category].append(score)
        print(f"  [{qid}] {query[:58]:<58} score={score:.2f}  {latency_ms:.0f}ms")

        results.append({
            "id": qid, "category": category, "query": query,
            "gold_path": gold_path, "score_method": score_method,
            "score": score, "retrieved_paths": paths[:TOP_K],
            "retrieval_ms": latency_ms, "system": "bm25",
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
        print(f"  {cat:<12} {avg:.4f}  (n={len(scores)}, weight={weight})")

    print(f"\n  weighted_total: {weighted_total:.4f}")
    gate = weighted_total >= 0.62 and all(v >= 0.50 for v in cat_avgs.values())
    print(f"  Phase 1 gate: {'✅ PASS' if gate else '❌ FAIL'}")

    output = {
        "run_label": run_label, "date": date_str, "system": "bm25",
        "n_queries": len(TEST_QUERIES),
        "category_scores": cat_avgs,
        "weighted_total": weighted_total,
        "phase1_gate_pass": gate,
        "results": results,
    }
    output_file.write_text(json.dumps(output, indent=2))
    print(f"\nSaved: {output_file}")

if __name__ == "__main__":
    main()
