"""
TREC-style independent gold suite builder.

Pools candidates from multiple retrieval systems, deduplicates, and grades
each (query, document) pair with the LLM judge to produce system-independent
relevance judgments.

Usage::

    kairix eval build-gold \\
        --suite suites/v2-real-world.yaml \\
        --output suites/v2-independent-gold.yaml \\
        --systems bm25-equal,bm25-qmd,bm25-title,vector

Methodology: TREC pooling (Voorhees & Harman, 2005) adapted for LLM judges.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kairix.eval.judge import JudgeResult, calibrate, fetch_llm_credentials, judge_batch

logger = logging.getLogger(__name__)

# BM25 weight presets for pooling — column order: filepath, title, doc
_WEIGHT_PRESETS: dict[str, tuple[float, float, float]] = {
    "bm25-equal": (1.0, 1.0, 1.0),
    "bm25-qmd": (10.0, 1.0, 1.0),
    "bm25-title": (1.0, 5.0, 1.0),
    "bm25-fp-title": (5.0, 3.0, 1.0),
}


@dataclass
class PooledCandidate:
    """A document candidate from the retrieval pool."""

    path: str
    title: str
    snippet: str
    collection: str
    sources: list[str] = field(default_factory=list)  # which systems retrieved it
    grade: int = 0  # LLM judge grade (0/1/2)
    grade_votes: list[int] = field(default_factory=list)  # grades from multiple runs


@dataclass
class GoldBuildReport:
    """Summary of gold suite building."""

    queries_processed: int = 0
    total_candidates_pooled: int = 0
    total_judge_calls: int = 0
    avg_candidates_per_query: float = 0.0
    grade_distribution: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0, 2: 0})


def _bm25_search_with_weights(
    query: str,
    weights: tuple[float, float, float],
    collections: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    """
    Run BM25 search with specific column weights.

    Returns list of {path, title, snippet, collection} dicts.
    """
    import re

    from kairix.db import get_db_path

    # Stop words (same as bm25.py)
    from kairix.search.bm25 import FTS_STOP_WORDS

    # Build FTS5 query
    raw = query.replace("-", " ").replace("_", " ").replace("'", " ").replace("\u2019", " ")
    tokens = re.findall(r"[a-zA-Z0-9]+", raw.lower())
    tokens = [t for t in tokens if t not in FTS_STOP_WORDS and len(t) >= 2]
    if not tokens:
        return []
    fts_query = " ".join(tokens)

    try:
        db_path = get_db_path()
        db = sqlite3.connect(str(db_path), timeout=5.0)
        db.row_factory = sqlite3.Row
    except Exception as e:
        logger.warning("gold_builder: cannot open DB — %s", e)
        return []

    w_fp, w_title, w_doc = weights
    try:
        if collections:
            placeholders = ",".join("?" * len(collections))
            # safe: float() cast on bm25 weights, no ? binding available for bm25 args
            sql = f"""
                SELECT d.collection, d.path, d.title, c.doc,
                       bm25(documents_fts, {float(w_fp)}, {float(w_title)}, {float(w_doc)}) AS score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                JOIN content c ON c.hash = d.hash
                WHERE documents_fts MATCH ?
                  AND d.collection IN ({placeholders})
                  AND d.active = 1
                ORDER BY score ASC
                LIMIT ?
            """
            params: list = [fts_query, *collections, limit]
        else:
            # safe: float() cast on bm25 weights, no ? binding available for bm25 args
            sql = f"""
                SELECT d.collection, d.path, d.title, c.doc,
                       bm25(documents_fts, {float(w_fp)}, {float(w_title)}, {float(w_doc)}) AS score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                JOIN content c ON c.hash = d.hash
                WHERE documents_fts MATCH ?
                  AND d.active = 1
                ORDER BY score ASC
                LIMIT ?
            """
            params = [fts_query, limit]

        rows = db.execute(sql, params).fetchall()
    except Exception as e:
        logger.warning("gold_builder: FTS query failed — %s", e)
        db.close()
        return []

    results = []
    for row in rows:
        doc_text = row["doc"] or ""
        if doc_text.startswith("---"):
            parts = doc_text.split("---", 2)
            snippet = parts[2].strip()[:300] if len(parts) >= 3 else doc_text[:300]
        else:
            snippet = doc_text[:300]
        results.append(
            {
                "path": str(row["path"]),
                "title": str(row["title"] or ""),
                "snippet": snippet,
                "collection": str(row["collection"]),
            }
        )

    db.close()
    return results


def _vector_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Run vector search. Returns list of {path, title, snippet, collection} dicts."""
    try:
        from kairix.db import open_db
        from kairix.search.hybrid import embed_text_as_bytes
        from kairix.search.vector import vector_search_bytes

        query_bytes = embed_text_as_bytes(query)
        if not query_bytes:
            return []

        db = open_db()
        results = vector_search_bytes(db, query_bytes, k=limit, collections=collections)
        db.close()

        return [
            {
                "path": r["path"],
                "title": r["title"],
                "snippet": r["snippet"][:300],
                "collection": r["collection"],
            }
            for r in results
        ]
    except Exception as e:
        logger.warning("gold_builder: vector search failed — %s", e)
        return []


def pool_candidates(
    query: str,
    systems: list[str],
    collections: list[str] | None = None,
    limit_per_system: int = 10,
) -> list[PooledCandidate]:
    """
    Pool top-k results from multiple retrieval systems for a single query.

    Deduplicates by path. Records which systems retrieved each document.
    """
    candidates: dict[str, PooledCandidate] = {}

    for system in systems:
        if system == "vector":
            results = _vector_search(query, collections, limit_per_system)
        elif system in _WEIGHT_PRESETS:
            results = _bm25_search_with_weights(query, _WEIGHT_PRESETS[system], collections, limit_per_system)
        else:
            logger.warning("gold_builder: unknown system %r — skipping", system)
            continue

        for r in results:
            path = r["path"]
            if path not in candidates:
                candidates[path] = PooledCandidate(
                    path=path,
                    title=r["title"],
                    snippet=r["snippet"],
                    collection=r["collection"],
                )
            candidates[path].sources.append(system)

    return list(candidates.values())


def grade_candidates(
    query: str,
    candidates: list[PooledCandidate],
    api_key: str,
    endpoint: str,
    deployment: str = "gpt-4o-mini",
    judge_runs: int = 2,
) -> list[PooledCandidate]:
    """
    Grade each candidate using the LLM judge.

    Runs judge_runs times and uses majority vote for final grade.
    """
    if not candidates:
        return candidates

    # Build (title_stem, snippet) pairs for judge
    judge_candidates = []
    for c in candidates:
        stem = Path(c.path).stem
        judge_candidates.append((stem, c.snippet[:150]))

    for _run in range(judge_runs):
        result: JudgeResult = judge_batch(
            query=query,
            candidates=judge_candidates,
            api_key=api_key,
            endpoint=endpoint,
            deployment=deployment,
            shuffle=True,
        )

        for c in candidates:
            stem = Path(c.path).stem
            grade = result.grades.get(stem, 0)
            c.grade_votes.append(grade)

    # Majority vote
    for c in candidates:
        if c.grade_votes:
            c.grade = max(set(c.grade_votes), key=c.grade_votes.count)

    return candidates


def build_independent_gold(
    suite_path: Path,
    output_path: Path,
    systems: list[str] | None = None,
    judge_runs: int = 2,
    calibrate_first: bool = True,
    limit_per_system: int = 10,
) -> GoldBuildReport:
    """
    Build an independent gold suite using TREC-style pooling + LLM judge.

    1. Load queries from existing suite
    2. For each query, pool candidates from multiple retrieval systems
    3. Grade each candidate with LLM judge (majority vote)
    4. Output enriched suite with system-independent gold_titles

    Args:
        suite_path:        Path to input suite YAML (queries + categories).
        output_path:       Path to write enriched suite YAML.
        systems:           List of retrieval system names to pool from.
        judge_runs:        Number of judge runs per query (default: 2).
        calibrate_first:   Run calibration anchors before judging (default: True).
        limit_per_system:  Top-k results per system per query (default: 10).

    Returns:
        GoldBuildReport with statistics.
    """
    if systems is None:
        systems = ["bm25-equal", "bm25-qmd", "bm25-title", "vector"]

    # Load suite
    with open(suite_path) as f:
        suite_data = yaml.safe_load(f)

    cases = suite_data.get("cases", [])
    if not cases:
        logger.error("gold_builder: no cases found in suite %s", suite_path)
        return GoldBuildReport()

    # Fetch credentials
    api_key, endpoint, deployment = fetch_llm_credentials()
    if not api_key or not endpoint:
        logger.error("gold_builder: no API credentials — cannot run judge")
        return GoldBuildReport()

    # Calibrate judge
    if calibrate_first:
        logger.info("gold_builder: running calibration...")
        calibrate(api_key, endpoint, deployment)
        logger.info("gold_builder: calibration passed")

    report = GoldBuildReport()

    for i, case in enumerate(cases):
        query = case.get("query", "")
        if not query:
            continue

        logger.info("gold_builder: [%d/%d] %s", i + 1, len(cases), query[:60])

        # Pool candidates
        candidates = pool_candidates(query, systems, limit_per_system=limit_per_system)
        report.total_candidates_pooled += len(candidates)

        if not candidates:
            logger.warning("gold_builder: no candidates for query %r", query[:60])
            continue

        # Grade with LLM judge
        candidates = grade_candidates(query, candidates, api_key, endpoint, deployment, judge_runs)
        report.total_judge_calls += len(candidates) * judge_runs

        # Build gold_titles from graded candidates (grade >= 1)
        gold_titles = []
        for c in sorted(candidates, key=lambda x: x.grade, reverse=True):
            report.grade_distribution[c.grade] = report.grade_distribution.get(c.grade, 0) + 1
            if c.grade >= 1:
                gold_titles.append(
                    {
                        "title": Path(c.path).stem,
                        "relevance": c.grade,
                    }
                )

        # Update case with independent gold
        case["gold_titles"] = gold_titles
        case["score_method"] = "ndcg"
        # Preserve original gold_paths for comparison but mark as legacy
        if "gold_paths" in case:
            case["legacy_gold_paths"] = case.pop("gold_paths")

        report.queries_processed += 1

    # Update metadata
    suite_data.setdefault("meta", {})["gold_method"] = "trec-pooling-llm-judge"
    suite_data["meta"]["gold_systems"] = systems
    suite_data["meta"]["judge_runs"] = judge_runs
    suite_data["meta"]["n_cases"] = report.queries_processed

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(suite_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    report.avg_candidates_per_query = (
        report.total_candidates_pooled / report.queries_processed if report.queries_processed > 0 else 0
    )

    logger.info("gold_builder: %s", report)
    return report
