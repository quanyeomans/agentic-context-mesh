#!/usr/bin/env python3
"""
Mnemosyne Benchmark Runner — Phase 0
Runs 50 test queries against QMD BM25 and scores results via LLM-as-judge.
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

QMD_SEARCH = str(Path.home() / ".openclaw/skills/qmd/qmd-search.sh")
BENCHMARK_DIR = Path("/data/obsidian-vault/01-Projects/202603-Mnemosyne")
RESULTS_DIR = BENCHMARK_DIR / "benchmark-results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

AZURE_ENDPOINT = None  # resolved at runtime from KV secret azure-openai-endpoint
AZURE_DEPLOYMENT = "gpt-4o-mini"
AZURE_API_VERSION = "2024-02-01"

CATEGORY_WEIGHTS = {
    "recall":      0.25,
    "temporal":    0.20,
    "entity":      0.20,
    "conceptual":  0.15,
    "multi_hop":   0.10,
    "procedural":  0.10,
}

# ── Test Suite ─────────────────────────────────────────────────────────────────
# Each entry: (id, category, query, gold, scoring_type)
# scoring_type: "exact" = gold doc path must appear in top-3 file paths
#               "llm"   = LLM-as-judge 0-3 score

TEST_QUERIES = [
    # ── Category 1: Recall (12 queries, weight 25%) ──
    ("R01", "recall", "Arize Phoenix observability recommendation",
     "01-Projects/202603-Arize-Observability-Research/RESEARCH-REPORT.md", "exact"),
    ("R02", "recall", "Docker daemon network Azure DNS",
     "04-Agent-Knowledge/shared/rules.md", "exact"),
    ("R03", "recall", "SPF record duplicate permerror",
     "04-Agent-Knowledge/shared/rules.md", "exact"),
    ("R04", "recall", "Alex Jordan voice profile",
     "02-Areas/Career/Brand/Tone-Of-Voice/Alex-Jordan-Voice-Profile.md", "exact"),
    ("R05", "recall", "QMD SQLite lock crash root cause",
     "04-Agent-Knowledge/shared/facts.md", "exact"),
    ("R06", "recall", "Mantel Group follow-up note",
     "01-Projects/202603-Mantel-Group-Deep-Research/FOLLOW-UP-NOTE.md", "exact"),
    ("R07", "recall", "Bower Bird managed identity migration",
     "Builder board card", "exact"),
    ("R08", "recall", "RALPH loop prompt implementation rules",
     "engineering-hub/ralph/prompts/PROMPT.md", "exact"),
    ("R09", "recall", "Telegram webhook auth 500 not 401",
     "Builder board backlog", "exact"),
    ("R10", "recall", "Apple calendar CalDAV recurring events bug",
     "04-Agent-Knowledge/shared/facts.md", "exact"),
    ("R11", "recall", "style-check em-dash hard violation",
     "04-Agent-Knowledge/shared/rules.md", "exact"),
    ("R12", "recall", "NemoClaw analysis OpenShell verdict",
     "01-Projects/202603-NemoClaw-Analysis/ANALYSIS.md", "exact"),

    # ── Category 2: Temporal (10 queries, weight 20%) ──
    ("T01", "temporal", "What did we decide about vault IA structure in March 2026?",
     "04-Agent-Knowledge/shared/decisions.md ADR-S03", "llm"),
    ("T02", "temporal", "What security fixes were completed last week?",
     "Builder board Done column, 2026-03-18 items", "llm"),
    ("T03", "temporal", "What was the most recent Shape memory log?",
     "memory/2026-03-22.md", "llm"),
    ("T04", "temporal", "What did Dan say about the MIT event in mid-March?",
     "01-Projects/MIT-CISR-Events/ + memory logs", "llm"),
    ("T05", "temporal", "Which builder tasks were escalated last week?",
     "Builder board In Progress, escalated::2026-03-21", "llm"),
    ("T06", "temporal", "What were the findings from the Vault IA review?",
     "01-Projects/202603-Vault-IA-Review/RESEARCH-REPORT.md", "llm"),
    ("T07", "temporal", "What did we add to the shared rules in March?",
     "04-Agent-Knowledge/shared/rules.md (recent entries)", "llm"),
    ("T08", "temporal", "When was the OpenClaw gateway crash and what caused it?",
     "04-Agent-Knowledge/shared/facts.md", "llm"),
    ("T09", "temporal", "What board cards were completed on 2026-03-22?",
     "Boards — Done items with completed::2026-03-22", "llm"),
    ("T10", "temporal", "What was the last thing Builder shipped?",
     "Builder board Done — most recent completed item", "llm"),

    # ── Category 3: Entity/Relationship (10 queries, weight 20%) ──
    ("E01", "entity", "What do we know about Mantel Group?",
     "Research report + follow-up note + board card", "llm"),
    ("E02", "entity", "What has Builder worked on this month?",
     "Board (all In Progress + Done), memory logs", "llm"),
    ("E03", "entity", "What is Alex's positioning for board roles?",
     "USER.md + voice profile + career area", "llm"),
    ("E04", "entity", "What have we done related to Microsoft?",
     "Multiple: NexusDigital references, M365, Azure, engineering hub", "llm"),
    ("E05", "entity", "What does Alex think about Triad Consulting advisory practice?",
     "Multiple: MEMORY, USER, research outputs", "llm"),
    ("E06", "entity", "What do we know about Langfuse?",
     "Arize research report (recommended for multi-tenant)", "llm"),
    ("E07", "entity", "What interactions have we had with Leslie?",
     "Board cards (Tennis Australia pitch), CRM notes", "llm"),
    ("E08", "entity", "What are the active security issues for Bower Bird?",
     "Builder board backlog (multiple p1 security cards)", "llm"),
    ("E09", "entity", "What is the QMD architecture and known issues?",
     "facts.md + ops-runbook + crash incident", "llm"),
    ("E10", "entity", "What has Consultant produced this month?",
     "Consultant board Done + research reports", "llm"),

    # ── Category 4: Conceptual (8 queries, weight 15%) ──
    ("C01", "conceptual", "How do we handle situations where an agent makes a mistake?",
     "rules.md — do first, explain after + correction patterns", "llm"),
    ("C02", "conceptual", "What's our approach to handling client data?",
     "data sovereignty rules, Key Vault patterns, no-credentials-to-disk", "llm"),
    ("C03", "conceptual", "How do agents hand off work to each other?",
     "delegation rules, board cards, brief format", "llm"),
    ("C04", "conceptual", "What's the right size for a research task?",
     "compression ratio table, effort estimates", "llm"),
    ("C05", "conceptual", "How should we prepare Dan for an important meeting?",
     "executive-research-prep skill + audience-persona-builder", "llm"),
    ("C06", "conceptual", "What's the risk of running agents with too much access?",
     "security audit findings, agent secret isolation ADR", "llm"),
    ("C07", "conceptual", "How do we decide whether to build or buy a capability?",
     "gstack analysis, lake/ocean framing, make vs buy patterns", "llm"),
    ("C08", "conceptual", "What makes a good Builder task card?",
     "AGENTS.md delegation rules, spec template, checkpoint protocol", "llm"),

    # ── Category 5: Multi-hop (5 queries, weight 10%) ──
    ("M01", "multi_hop",
     "What observability tooling should we use for a client deployment vs our own platform?",
     "Arize research (Langfuse for multi-tenant, Phoenix for internal)", "llm"),
    ("M02", "multi_hop",
     "Does our current security posture meet what we'd recommend to a client?",
     "Security audit gaps + Triad Consulting advisory positioning", "llm"),
    ("M03", "multi_hop",
     "What Builder work would benefit from the browse-daemon?",
     "Browse-daemon spec (unlocks X Bookmark Sync) + X Content Pipeline spec", "llm"),
    ("M04", "multi_hop",
     "What knowledge would be most useful to include in an Alex Jordan speaker profile?",
     "Voice profile + career notes + MIT event research + board positioning", "llm"),
    ("M05", "multi_hop",
     "What did we learn from the MIT event that could strengthen our GTM?",
     "MIT research report + attendee briefing + post-event follow-up backlog", "llm"),

    # ── Category 6: Procedural (5 queries, weight 10%) ──
    ("P01", "procedural", "How do I run a style check on a deliverable before sending?",
     "document-tools skill + STYLE.md", "llm"),
    ("P02", "procedural", "What's the process for delegating work to Builder?",
     "AGENTS.md + spec template + engineering-hub", "llm"),
    ("P03", "procedural", "How should I capture a decision after a Dan conversation?",
     "Shared rules — Capture Agreed Decisions Immediately", "llm"),
    ("P04", "procedural", "What do I do if the gateway goes into a crash loop?",
     "04-Agent-Knowledge/shared/facts.md — QMD crash recovery", "llm"),
    ("P05", "procedural", "How do I fetch a secret from Key Vault?",
     "Multiple AGENTS.md + tools.md — az keyvault secret show pattern", "llm"),
]

JUDGE_PROMPT = """You are evaluating a memory retrieval system. Given:
- QUERY: the question asked
- GOLD: the expected answer / source documents
- RETRIEVED: what the system returned (top 3 results, with snippets)

Score from 0 to 3:
3 = Retrieved contains the gold answer or all gold sources. Agent could answer correctly.
2 = Retrieved contains partial gold. Agent might answer correctly with some gaps.
1 = Retrieved has tangentially related content. Unlikely to produce correct answer.
0 = Retrieved is irrelevant or empty.

Output ONLY a JSON object:
{"score": <0-3>, "reason": "<one sentence>", "missing": "<what was missing if score < 3>"}"""


# ── Azure OpenAI client ────────────────────────────────────────────────────────

def get_kv_secret(name: str) -> str:
    """Fetch a secret from Azure Key Vault."""
    result = subprocess.run(
        ["az", "keyvault", "secret", "show",
         "--vault-name", "kv-tc-exp",
         "--name", name,
         "--query", "value",
         "-o", "tsv"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get KV secret '{name}': {result.stderr}")
    return result.stdout.strip()

def get_api_key() -> str:
    return get_kv_secret("azure-openai-api-key")

def get_endpoint() -> str:
    return get_kv_secret("azure-openai-endpoint").rstrip("/") + "/"


def llm_judge(api_key: str, query: str, gold: str, retrieved_text: str) -> dict:
    """Call Azure OpenAI gpt-4o-mini to score a retrieval result."""
    endpoint = get_endpoint()
    url = (f"{endpoint}openai/deployments/{AZURE_DEPLOYMENT}"
           f"/chat/completions?api-version={AZURE_API_VERSION}")

    user_content = (
        f"QUERY: {query}\n\n"
        f"GOLD: {gold}\n\n"
        f"RETRIEVED:\n{retrieved_text}"
    )

    payload = {
        "messages": [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": 200,
    }

    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Parse JSON from response
        # Sometimes the model wraps it in ```json ... ```
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content)
        return {
            "score": int(result.get("score", 0)),
            "reason": result.get("reason", ""),
            "missing": result.get("missing", ""),
        }
    except Exception as e:
        return {"score": 0, "reason": f"error: {e}", "missing": "judge failed"}


# ── QMD search ────────────────────────────────────────────────────────────────

def run_qmd_search(query: str, limit: int = 3) -> list[dict]:
    """Run qmd BM25 search, return top-N results as list of dicts."""
    result = subprocess.run(
        [QMD_SEARCH, "search", query, "--json", "--limit", str(limit)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ⚠️  qmd-search failed: {result.stderr[:200]}", file=sys.stderr)
        return []
    try:
        data = json.loads(result.stdout)
        return data[:limit] if isinstance(data, list) else []
    except json.JSONDecodeError:
        print(f"  ⚠️  Could not parse qmd JSON output", file=sys.stderr)
        return []


def format_results_for_judge(results: list[dict]) -> str:
    """Format top-3 results into a text block for the LLM judge."""
    if not results:
        return "(no results returned)"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('file', 'unknown')} (score: {r.get('score', 0):.2f})")
        lines.append(f"    Title: {r.get('title', '')}")
        snippet = r.get("snippet", "").replace("\n", " ")[:300]
        lines.append(f"    Snippet: {snippet}")
    return "\n".join(lines)


def check_exact_match(gold: str, results: list[dict]) -> dict:
    """
    For Recall category: check if gold document path appears in any top-3 result file path.
    Score: 3 if match, 0 if not.
    Handles special cases like 'Builder board card' (no real path to match).
    """
    # Special cases where gold is a board card or memory — hard to exact-match
    special = ["board card", "board backlog", "Builder board"]
    if any(s.lower() in gold.lower() for s in special):
        # We'll do a soft keyword check instead
        gold_keywords = set(gold.lower().split())
        for r in results:
            file_lower = r.get("file", "").lower()
            title_lower = r.get("title", "").lower()
            snippet_lower = r.get("snippet", "").lower()
            combined = file_lower + " " + title_lower + " " + snippet_lower
            hits = sum(1 for kw in gold_keywords if len(kw) > 4 and kw in combined)
            if hits >= 2:
                return {"score": 3, "reason": "keyword match on board card gold", "missing": ""}
        return {"score": 0, "reason": "board card gold not found in results", "missing": gold}

    # Normalise the gold path: lowercase, replace backslash
    gold_norm = gold.lower().replace("\\", "/")
    for r in results:
        file_path = r.get("file", "").lower()
        if gold_norm in file_path or file_path.endswith(gold_norm):
            return {"score": 3, "reason": "exact match found in top-3", "missing": ""}

    # Partial path match (last 2 path segments)
    gold_parts = [p for p in gold_norm.split("/") if p]
    if len(gold_parts) >= 2:
        gold_tail = "/".join(gold_parts[-2:])
        for r in results:
            if gold_tail in r.get("file", "").lower():
                return {"score": 3, "reason": "partial path match found", "missing": ""}

    return {"score": 0, "reason": "gold document not found in top-3 results", "missing": gold}


# ── Main runner ───────────────────────────────────────────────────────────────

def run_benchmark(system: str = "qmd-bm25") -> dict:
    today = datetime.date.today().isoformat()
    run_id = f"B0-{system}-{today}"

    print(f"\n{'='*60}")
    print(f"  Mnemosyne Benchmark — {run_id}")
    print(f"  System: {system}")
    print(f"  Queries: {len(TEST_QUERIES)}")
    print(f"{'='*60}\n")

    # Get API key upfront
    print("Fetching Azure OpenAI API key from Key Vault...", flush=True)
    api_key = get_api_key()
    print("✓ API key retrieved\n", flush=True)

    # Organise results by category
    category_details = {cat: [] for cat in CATEGORY_WEIGHTS}

    for qid, category, query, gold, scoring_type in TEST_QUERIES:
        print(f"[{qid}] {query[:70]}...", flush=True)

        t0 = time.time()
        results = run_qmd_search(query, limit=3)
        latency_ms = int((time.time() - t0) * 1000)

        retrieved_text = format_results_for_judge(results)
        retrieved_files = [r.get("file", "") for r in results]

        if scoring_type == "exact":
            judgment = check_exact_match(gold, results)
        else:
            # LLM judge
            judgment = llm_judge(api_key, query, gold, retrieved_text)

        score = judgment["score"]
        emoji = "✓" if score == 3 else ("~" if score >= 1 else "✗")
        print(f"  {emoji} Score: {score}/3 | {judgment['reason'][:80]}", flush=True)

        category_details[category].append({
            "id": qid,
            "query": query,
            "gold": gold,
            "scoring_type": scoring_type,
            "retrieved_files": retrieved_files,
            "retrieved_text": retrieved_text[:500],
            "score": score,
            "reason": judgment["reason"],
            "missing": judgment.get("missing", ""),
            "latency_ms": latency_ms,
        })

        # Small rate-limit pause for LLM calls
        if scoring_type == "llm":
            time.sleep(0.5)

    # ── Compute aggregate scores ──
    scores = {}
    for cat, weight in CATEGORY_WEIGHTS.items():
        details = category_details[cat]
        n = len(details)
        if n == 0:
            scores[cat] = {"score": 0.0, "n": 0, "detail": []}
            continue
        raw_sum = sum(d["score"] for d in details)
        # normalise: each query max score is 3
        avg_normalised = raw_sum / (n * 3)
        scores[cat] = {
            "score": round(avg_normalised, 4),
            "n": n,
            "detail": details,
        }

    weighted_total = sum(
        CATEGORY_WEIGHTS[cat] * scores[cat]["score"]
        for cat in CATEGORY_WEIGHTS
    )

    output = {
        "run_id": run_id,
        "system": system,
        "date": today,
        "scores": scores,
        "weighted_total": round(weighted_total, 4),
        "latency_p50_ms": None,
        "latency_p95_ms": None,
    }

    # ── Write JSON results ──
    out_path = RESULTS_DIR / f"B0-{system}-{today}.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n✓ Results written to: {out_path}\n", flush=True)

    # ── Print summary table ──
    print_summary(scores, weighted_total)

    return output


def print_summary(scores: dict, weighted_total: float):
    cat_labels = {
        "recall":     "Recall",
        "temporal":   "Temporal",
        "entity":     "Entity/Rel",
        "conceptual": "Conceptual",
        "multi_hop":  "Multi-hop",
        "procedural": "Procedural",
    }

    header = f"{'Category':<14} {'Queries':>7}  {'Avg Score':>9}  {'Weight':>6}  {'Weighted':>8}"
    rule = "─" * len(header)
    print(rule)
    print(header)
    print(rule)
    for cat, weight in CATEGORY_WEIGHTS.items():
        s = scores[cat]
        weighted = weight * s["score"]
        label = cat_labels.get(cat, cat)
        print(f"{label:<14} {s['n']:>7}  {s['score']:>9.4f}  {weight:>6.0%}  {weighted:>8.4f}")
    print(rule)
    print(f"{'TOTAL':<14} {'':>7}  {'':>9}  {'':>6}  {weighted_total:>8.4f}")
    print(rule)
    print()


if __name__ == "__main__":
    system = sys.argv[1] if len(sys.argv) > 1 else "qmd-bm25"
    run_benchmark(system)
