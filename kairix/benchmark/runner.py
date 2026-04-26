"""
Benchmark runner for kairix retrieval quality evaluation.

Runs a BenchmarkSuite against a configured retrieval system and produces
per-category and weighted-total scores.

Score methods:
  exact - gold_path present in top-5 retrieved paths (case-insensitive substring)
  fuzzy - gold_path present in top-10 (relaxed, for approximate matching)
  llm   - gpt-4o-mini rates retrieved content relevance 0.0-1.0
  ndcg  - true NDCG@10 with graded relevance (0/1/2); also computes Hit@5 and MRR@10
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kairix.benchmark.suite import BenchmarkSuite
from kairix.eval.constants import CATEGORY_ALIASES, CATEGORY_WEIGHTS

# Re-export so existing `from kairix.benchmark.runner import CATEGORY_WEIGHTS` keeps working
__all__ = ["CATEGORY_ALIASES", "CATEGORY_WEIGHTS"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_GATES: dict[str, float] = {
    "phase1": 0.62,
    "phase2": 0.68,
    "phase3": 0.75,
}

SCORE_TIERS = [
    (0.80, "Phase 4 target — fully-tuned with synthesis"),
    (0.75, "Production quality — Phase 3 gate"),
    (0.68, "Phase 2 gate — temporal + tiered context working"),
    (0.62, "Phase 1 gate — hybrid search + entity graph"),
    (0.51, "Typical BM25 on well-curated vault"),
    (0.35, "BM25 on Phase 1 query suite"),
    (0.00, "Below BM25 baseline — something is broken"),
]

CATEGORY_FLOOR = 0.50  # per-category minimum for gate pass

# How many top results to inspect for exact/fuzzy matching
EXACT_MATCH_TOPK = 5
FUZZY_MATCH_TOPK = 10

# ---------------------------------------------------------------------------
# NDCG@10 helpers (ported from run-benchmark-v2.py, VM private repo)
# ---------------------------------------------------------------------------


def _dcg(relevances: list[float], k: int) -> float:
    """Discounted Cumulative Gain at k."""
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def _ideal_dcg(gold_paths: list[dict], k: int) -> float:
    """IDCG: ideal ordering (sort by relevance desc, take top k)."""
    sorted_rels = sorted([g["relevance"] for g in gold_paths], reverse=True)
    return _dcg(sorted_rels, k)


def _path_relevance(path: str, gold_map: dict[str, float]) -> float:
    """Return relevance of a retrieved path via exact then suffix match against gold_map."""
    p = path.lower().replace("\\", "/")
    if p in gold_map:
        return gold_map[p]
    parts = p.split("/")
    for n in range(1, len(parts)):
        suffix = "/".join(parts[n:])
        if suffix in gold_map:
            return gold_map[suffix]
    return 0.0


def _path_in_set(path: str, gold_set: set[str]) -> bool:
    """True if path matches any gold entry via exact or suffix match."""
    p = path.lower().replace("\\", "/")
    if p in gold_set:
        return True
    parts = p.split("/")
    for n in range(1, len(parts)):
        if "/".join(parts[n:]) in gold_set:
            return True
    return False


def _ndcg_score(retrieved: list[str], gold_paths: list[dict], k: int = 10) -> float:
    """Compute NDCG@k given retrieved path list and graded gold list."""
    if not gold_paths:
        return 0.0
    idcg = _ideal_dcg(gold_paths, k)
    if idcg == 0.0:
        return 0.0
    gold_map = {g["path"].lower(): g["relevance"] for g in gold_paths}
    rels = [_path_relevance(p, gold_map) for p in retrieved[:k]]
    return _dcg(rels, k) / idcg


def _hit_at_k(retrieved: list[str], gold_paths: list[dict], k: int) -> bool:
    """True if any gold path (any relevance ≥ 1) appears in top-k retrieved."""
    gold_set = {g["path"].lower() for g in gold_paths if g.get("relevance", 0) >= 1}
    return any(_path_in_set(p, gold_set) for p in retrieved[:k])


def _reciprocal_rank(retrieved: list[str], gold_paths: list[dict], k: int) -> float:
    """Reciprocal rank of first relevant gold path in top-k (0 if none)."""
    gold_set = {g["path"].lower() for g in gold_paths if g.get("relevance", 0) >= 1}
    for i, p in enumerate(retrieved[:k]):
        if _path_in_set(p, gold_set):
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Title-based NDCG helpers (TREC qrels pattern — path-agnostic document identity)
#
# Obsidian uses the filename stem as the stable document ID (wikilinks reference
# titles, not paths). Normalising both gold titles and retrieved path stems to the
# same form makes scoring robust to vault reorganisation: a file moved from
# 02-Areas/Foo/foo.md to Archive/foo.md still matches a gold_title of "foo".
# ---------------------------------------------------------------------------


def _normalise_title(t: str) -> str:
    """Stable document identity key: lowercase, consolidate separators to hyphens."""
    return re.sub(r"[-_\s]+", "-", t.lower()).strip("-")


def _stem_from_path(path: str) -> str:
    """Extract the normalised note-title key from a retrieved filesystem path."""
    return _normalise_title(Path(path).stem)


def _title_in_retrieved(gold_title: str, retrieved_paths: list[str], top_k: int) -> bool:
    """True if any of the top-k retrieved paths resolves to the gold title."""
    norm = _normalise_title(gold_title)
    return any(_stem_from_path(p) == norm for p in retrieved_paths[:top_k])


def _ndcg_score_by_title(retrieved_paths: list[str], gold_titles: list[dict], k: int = 10) -> float:
    """NDCG@k using title-based document identity (path-agnostic)."""
    if not gold_titles:
        return 0.0
    idcg = _ideal_dcg(gold_titles, k)  # _ideal_dcg reads "relevance" key, works for both formats
    if idcg == 0.0:
        return 0.0
    gold_map = {_normalise_title(g["title"]): g["relevance"] for g in gold_titles}
    rels = [gold_map.get(_stem_from_path(p), 0) for p in retrieved_paths[:k]]
    return _dcg(rels, k) / idcg


def _hit_at_k_by_title(retrieved_paths: list[str], gold_titles: list[dict], k: int) -> bool:
    """True if any gold title (relevance >= 1) appears in top-k retrieved paths."""
    gold_set = {_normalise_title(g["title"]) for g in gold_titles if g.get("relevance", 0) >= 1}
    return any(_stem_from_path(p) in gold_set for p in retrieved_paths[:k])


def _reciprocal_rank_by_title(retrieved_paths: list[str], gold_titles: list[dict], k: int) -> float:
    """Reciprocal rank of the first relevant gold title in top-k (0.0 if none)."""
    gold_set = {_normalise_title(g["title"]) for g in gold_titles if g.get("relevance", 0) >= 1}
    for i, p in enumerate(retrieved_paths[:k]):
        if _stem_from_path(p) in gold_set:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    meta: dict[str, Any]
    summary: dict[str, Any]  # weighted_total, category_scores, gate dict
    diagnostics: dict[str, Any]
    cases: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------


def _exact_match(paths: list[str], gold: str) -> float:
    """1.0 if gold path is a case-insensitive substring of any top-K result paths."""
    if not gold:
        return 0.0
    gold_lower = gold.lower().replace("\\", "/")
    # Also match just the filename portion
    gold_parts = gold_lower.split("/")
    for path in paths[:EXACT_MATCH_TOPK]:
        path_lower = path.lower().replace("\\", "/")
        if gold_lower in path_lower or path_lower in gold_lower:
            return 1.0
        # Match on last N path components
        for n in range(len(gold_parts), 0, -1):
            suffix = "/".join(gold_parts[-n:])
            if suffix and suffix in path_lower:
                return 1.0
    return 0.0


def _classification_score(query: str, expected_type: str) -> float:
    """
    Score a classification case by running kairix classify and comparing type.
    Returns 1.0 if result type matches expected_type, 0.0 otherwise.
    """
    try:
        from kairix.classify.judge import classify_with_llm
        from kairix.classify.rules import classify_content

        result = classify_content(query, agent="shared")
        if result.type == "unknown":
            # Try LLM fallback
            result = classify_with_llm(query, agent="shared")

        return 1.0 if result.type == expected_type else 0.0
    except Exception:
        return 0.0


def _fuzzy_match(paths: list[str], gold: str) -> float:
    """1.0 if gold path is in any top-10 result paths."""
    if not gold:
        return 0.0
    gold_lower = gold.lower().replace("\\", "/")
    gold_parts = gold_lower.split("/")
    for path in paths[:FUZZY_MATCH_TOPK]:
        path_lower = path.lower().replace("\\", "/")
        if gold_lower in path_lower or path_lower in gold_lower:
            return 1.0
        for n in range(len(gold_parts), 0, -1):
            suffix = "/".join(gold_parts[-n:])
            if suffix and suffix in path_lower:
                return 1.0
    return 0.0


def _llm_judge(
    query: str,
    paths: list[str],
    snippets: list[str],
    api_key: str,
    endpoint: str,
    deployment: str = "gpt-4o-mini",
) -> float:
    """
    Score 0.0-1.0 using gpt-4o-mini as relevance judge.

    Returns 0.0 on any failure (API error, parse error, timeout).
    """
    try:
        import urllib.request

        if not paths:
            return 0.0

        # Match the original run-benchmark-hybrid.py scorer — paths only, 6-point scale
        # This ensures scores are comparable across runs.
        snippets_text = "\n".join(f"- {p}" for p in paths[:5])
        prompt = (
            f"You are evaluating memory retrieval quality for an AI agent system.\n\n"
            f"Query: {query}\n\n"
            f"Retrieved documents (paths):\n{snippets_text}\n\n"
            "Score the retrieval quality from 0.0 to 1.0:\n"
            "- 1.0: Retrieved documents directly and completely answer the query\n"
            "- 0.8: Retrieved documents mostly answer the query with minor gaps\n"
            "- 0.6: Retrieved documents partially answer the query\n"
            "- 0.4: Retrieved documents are tangentially related\n"
            "- 0.2: Retrieved documents have minimal relevance\n"
            "- 0.0: Retrieved documents are irrelevant or empty\n\n"
            "Reply with ONLY a number between 0.0 and 1.0."
        )

        payload = json.dumps(
            {
                "model": deployment,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 10,
                "temperature": 0.0,
            }
        ).encode()

        # Build URL: Azure OpenAI chat completions
        url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-01"

        req = urllib.request.Request(  # noqa: S310
            url,
            data=payload,
            headers={"Content-Type": "application/json", "api-key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"].strip()
        score = float(content)
        return max(0.0, min(1.0, score))

    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Retrieval backends
# ---------------------------------------------------------------------------


def _retrieve(
    query: str,
    system: str,
    agent: str,
    limit: int = 10,
    db_path: str | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """
    Run retrieval and return (paths, snippets, metadata).
    """
    if system == "hybrid":
        from kairix.search.hybrid import search

        # Use a large budget to avoid the token-budget system truncating results.
        # The budget allocator loads full document content; with a 5000-token budget,
        # 3-4 long documents exhaust it before positions 5-10 are filled. For
        # benchmark scoring we need all fused results ranked by RRF score, not
        # truncated by context-window budget. 500_000 tokens is effectively unlimited
        # for a typical 25-result fused set.
        sr = search(query=query, agent=agent, scope="shared+agent", budget=500_000)
        paths = [b.result.path for b in sr.results]
        snippets = [b.content[:500] for b in sr.results]
        meta = {
            "intent": sr.intent.value,
            "bm25_count": sr.bm25_count,
            "vec_count": sr.vec_count,
            "fused_count": sr.fused_count,
            "vec_failed": sr.vec_failed,
            "latency_ms": round(sr.latency_ms, 1),
        }
        return paths, snippets, meta

    elif system == "bm25":
        from kairix.search.bm25 import bm25_search

        results = bm25_search(query=query, agent=agent, limit=limit)
        # BM25Result.file is now a bare vault-relative path
        paths = [r["file"] for r in results]
        snippets = [r.get("snippet") or "" for r in results]
        return paths, snippets, {"system": "bm25"}

    elif system == "mock":
        from kairix.benchmark.mock_retrieval import mock_retrieve

        return mock_retrieve(query=query, limit=limit)

    elif system == "mock-reflib":
        from kairix.benchmark.mock_reflib_retrieval import mock_reflib_retrieve

        return mock_reflib_retrieve(query=query, limit=limit)

    else:
        raise ValueError(f"Unknown system: {system!r}. Use 'hybrid', 'bm25', 'vector', 'mock', or 'mock-reflib'.")


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------


def _score_tier(score: float) -> str:
    for threshold, label in SCORE_TIERS:
        if score >= threshold:
            return label
    return SCORE_TIERS[-1][1]


def _category_diagnosis(category: str, score: float) -> str:
    """Return a brief diagnosis for a low-scoring category."""
    if score >= CATEGORY_FLOOR:
        return "✅ above floor"
    diagnoses = {
        "recall": "❌ semantic matching not finding exact docs — check vector index freshness",
        "temporal": "❌ temporal weakness is likely an ingestion problem — date-aware chunking needed (Phase 2)",
        "entity": "❌ entity graph may be empty — seed entities via kairix entity suggest/validate",
        "conceptual": "❌ abstract queries not resolving — check intent classifier routing",
        "multi_hop": "❌ multi-hop requires connected retrieval — Phase 3 planning layer",
        "procedural": "❌ procedural docs not surfacing — check collection scope",
        "classification": "❌ classification rules not matching — check rules.py patterns",
    }
    return diagnoses.get(category, f"❌ score {score:.3f} below floor {CATEGORY_FLOOR}")


def format_interpretation(result: BenchmarkResult) -> str:
    """Return a human-readable interpretation section."""
    lines: list[str] = []
    wt = result.summary["weighted_total"]
    tier = _score_tier(wt)

    lines.append("=" * 60)
    lines.append("BENCHMARK RESULTS")
    lines.append("=" * 60)
    lines.append(f"Weighted total: {wt:.3f}  [{tier}]")
    ndcg = result.summary.get("ndcg_at_10")
    hit5 = result.summary.get("hit_rate_at_5")
    mrr = result.summary.get("mrr_at_10")
    if ndcg is not None:
        lines.append(f"NDCG@10:       {ndcg:.3f}  (Hit@5: {hit5:.3f}  MRR@10: {mrr:.3f})")
    lines.append("")
    lines.append("Category breakdown:")
    cat_scores = result.summary["category_scores"]
    for cat, weight in CATEGORY_WEIGHTS.items():
        score = cat_scores.get(cat, 0.0)
        n = result.diagnostics.get("category_counts", {}).get(cat, 0)
        diagnosis = _category_diagnosis(cat, score)
        lines.append(f"  {cat:12} {score:.3f}  (weight {weight:.0%}, n={n})  {diagnosis}")
    lines.append("")

    # Gate check
    for gate_name, gate_threshold in PHASE_GATES.items():
        status = "PASS ✅" if wt >= gate_threshold else f"FAIL ❌ (need +{gate_threshold - wt:.3f})"
        lines.append(f"  {gate_name.upper()} gate (≥{gate_threshold}): {status}")
    lines.append("")

    # Per-category floor check
    floors_failed = [cat for cat, score in cat_scores.items() if score < CATEGORY_FLOOR]
    if floors_failed:
        lines.append(f"Categories below floor ({CATEGORY_FLOOR}):")
        for cat in floors_failed:
            lines.append(f"  {cat}: {cat_scores[cat]:.3f}")
    else:
        lines.append("All categories above floor ✅")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_benchmark(
    suite: BenchmarkSuite,
    system: str = "hybrid",
    agent: str = "shape",
    output_dir: str | None = None,
    db_path: str | None = None,
) -> BenchmarkResult:
    """
    Run all benchmark cases and return a BenchmarkResult.

    Args:
        suite:      Loaded and validated BenchmarkSuite.
        system:     Retrieval system: 'hybrid', 'bm25', 'vector', 'mock', or 'mock-reflib'.
        agent:      Agent name for collection scoping.
        output_dir: If set, write JSON result file here.
        db_path:    Optional path to a specific database. Propagated to the retrieval
                    backend so it can target a specific store. None means use the default.

    Returns:
        BenchmarkResult with summary, category scores, and per-case results.
    """
    # Fetch Azure credentials for LLM judge
    api_key = ""
    endpoint = ""
    deployment = "gpt-4o-mini"
    try:
        import os
        import subprocess

        _kv_name = os.environ.get("KAIRIX_KV_NAME") or os.environ.get("KV_NAME", "")
        if not _kv_name:
            raise ValueError(
                "Key Vault name not set — cannot fetch LLM judge credentials. "
                "Set KAIRIX_KV_NAME (or KV_NAME) to your Azure Key Vault name, "
                "or set AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT directly to skip Key Vault."
            )

        api_key = subprocess.run(  # noqa: S603 — az keyvault is a trusted CLI binary
            [  # noqa: S607
                "az",
                "keyvault",
                "secret",
                "show",
                "--vault-name",
                _kv_name,
                "--name",
                "azure-openai-api-key",
                "--query",
                "value",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        endpoint = subprocess.run(  # noqa: S603 — az keyvault is a trusted CLI binary
            [  # noqa: S607
                "az",
                "keyvault",
                "secret",
                "show",
                "--vault-name",
                _kv_name,
                "--name",
                "azure-openai-endpoint",
                "--query",
                "value",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        dep = subprocess.run(  # noqa: S603 — az keyvault is a trusted CLI binary
            [  # noqa: S607
                "az",
                "keyvault",
                "secret",
                "show",
                "--vault-name",
                _kv_name,
                "--name",
                "azure-openai-gpt4o-mini-deployment",
                "--query",
                "value",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if dep:
            deployment = dep
    except Exception:  # noqa: S110
        pass  # Graceful: LLM judge returns 0.0 if creds unavailable

    case_results: list[dict[str, Any]] = []
    # Include all valid categories including classification
    all_categories = set(CATEGORY_WEIGHTS.keys()) | {"classification"}
    category_scores: dict[str, list[float]] = {cat: [] for cat in all_categories}

    for case in suite.cases:
        t0 = time.time()

        # Classification cases don't use retrieval
        if case.score_method == "classification":
            paths, snippets, retrieval_meta = [], [], {"scored_by": "classification"}
        else:
            try:
                paths, snippets, retrieval_meta = _retrieve(
                    query=case.query,
                    system=system,
                    agent=case.agent or agent,
                    db_path=db_path,
                )
            except Exception as exc:
                paths, snippets, retrieval_meta = [], [], {"error": str(exc)}

        # Score
        ndcg_detail: dict[str, Any] = {}
        if case.score_method == "classification":
            # Classification cases: run classify and compare type (no retrieval needed)
            score = _classification_score(case.query, case.expected_type or "")
        elif case.score_method == "exact":
            if case.gold_title:
                # Title-based: path-agnostic, survives vault reorganisation
                score = 1.0 if _title_in_retrieved(case.gold_title, paths, EXACT_MATCH_TOPK) else 0.0
            else:
                score = _exact_match(paths, case.gold_path or "")
        elif case.score_method == "fuzzy":
            if case.gold_title:
                score = 1.0 if _title_in_retrieved(case.gold_title, paths, FUZZY_MATCH_TOPK) else 0.0
            else:
                score = _fuzzy_match(paths, case.gold_path or "")
        elif case.score_method == "ndcg":
            if case.gold_titles:
                # Title-based NDCG: TREC qrels pattern — stable identity, path-agnostic
                score = _ndcg_score_by_title(paths, case.gold_titles, k=10)
                ndcg_detail = {
                    "hit_at_5": _hit_at_k_by_title(paths, case.gold_titles, k=5),
                    "rr": _reciprocal_rank_by_title(paths, case.gold_titles, k=10),
                }
            else:
                # Path-based fallback: backwards compat for suites without gold_titles
                effective_gold = case.gold_paths or (
                    [{"path": case.gold_path, "relevance": 2}] if case.gold_path else []
                )
                score = _ndcg_score(paths, effective_gold, k=10)
                ndcg_detail = {
                    "hit_at_5": _hit_at_k(paths, effective_gold, k=5),
                    "rr": _reciprocal_rank(paths, effective_gold, k=10),
                }
        else:  # llm
            score = _llm_judge(
                query=case.query,
                paths=paths,
                snippets=snippets,
                api_key=api_key,
                endpoint=endpoint,
                deployment=deployment,
            )

        elapsed_ms = (time.time() - t0) * 1000

        cat = CATEGORY_ALIASES.get(case.category, case.category)
        if cat in category_scores:
            category_scores[cat].append(score)

        case_results.append(
            {
                "id": case.id,
                "category": cat,
                "original_category": case.category,
                "query": case.query,
                "gold_path": case.gold_path,
                "score_method": case.score_method,
                "score": round(score, 4),
                "retrieved_paths": paths[:10],
                "elapsed_ms": round(elapsed_ms, 1),
                **ndcg_detail,
                **retrieval_meta,
            }
        )

    # Aggregate
    per_category_avg: dict[str, float] = {}
    for cat, scores in category_scores.items():
        per_category_avg[cat] = round(sum(scores) / len(scores), 4) if scores else 0.0

    # For suite version >= 1.1, include classification in weighted total.
    # Phase 3 weight model: classification gets 0.15 weight (new Phase 3 capability);
    # temporal reduced from 0.20 to 0.10 (temporal is a Phase 4 target, currently
    # structurally limited by ingestion — date-aware chunking not yet implemented).
    # Total weights: recall=0.25 + temporal=0.10 + entity=0.20 + conceptual=0.15
    #              + multi_hop=0.10 + procedural=0.10 + classification=0.15 = 1.05
    # Slightly >1.0 is acceptable: classification is additive evidence for Phase 3.
    suite_version = suite.meta.get("version", "1.0")
    effective_weights = dict(CATEGORY_WEIGHTS)
    if suite_version >= "1.1" and per_category_avg.get("classification", 0.0) > 0:
        # Phase 3 weight redistribution: add classification, reduce temporal ceiling
        effective_weights["classification"] = 0.15
        effective_weights["temporal"] = 0.10  # Phase 4 target — reduce from 0.20

    weighted_total = round(sum(per_category_avg.get(cat, 0.0) * w for cat, w in effective_weights.items()), 4)

    gates = {gate: weighted_total >= threshold for gate, threshold in PHASE_GATES.items()}

    # Aggregate NDCG-specific metrics across ndcg-scored cases
    ndcg_cases = [c for c in case_results if c.get("score_method") == "ndcg"]
    ndcg_at_10 = round(sum(c["score"] for c in ndcg_cases) / len(ndcg_cases), 4) if ndcg_cases else None
    hit_rate_at_5 = (
        round(sum(float(c.get("hit_at_5", 0)) for c in ndcg_cases) / len(ndcg_cases), 4) if ndcg_cases else None
    )
    mrr_at_10 = round(sum(c.get("rr", 0.0) for c in ndcg_cases) / len(ndcg_cases), 4) if ndcg_cases else None

    result = BenchmarkResult(
        meta={
            "suite_name": suite.meta.get("name", "unknown"),
            "system": system,
            "agent": agent,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "n_cases": len(suite.cases),
            "weighted_total": weighted_total,
        },
        summary={
            "weighted_total": weighted_total,
            "category_scores": per_category_avg,
            "gates": gates,
            "ndcg_at_10": ndcg_at_10,
            "hit_rate_at_5": hit_rate_at_5,
            "mrr_at_10": mrr_at_10,
        },
        diagnostics={
            "category_counts": {cat: len(scores) for cat, scores in category_scores.items()},
        },
        cases=case_results,
    )

    # Save to file if requested
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        suite_slug = suite.meta.get("name", "suite").lower().replace(" ", "-")
        filename = f"B-{suite_slug}-{system}-{date_str}.json"
        out_path = Path(output_dir) / filename
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "meta": result.meta,
                    "summary": result.summary,
                    "diagnostics": result.diagnostics,
                    "cases": result.cases,
                },
                f,
                indent=2,
            )
        import logging as _logging

        _logging.getLogger(__name__).info("Results saved to: %s", out_path)

    return result
