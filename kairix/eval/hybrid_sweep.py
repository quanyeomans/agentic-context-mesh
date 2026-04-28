"""
Hybrid pipeline calibration sweep — grid search over RRF k, boost configs,
and retrieval modes against an independent gold suite.

Evaluates the full hybrid pipeline (BM25 + vector + RRF + boosts) with
different parameter combinations. Designed to answer:

1. Does vector search help or hurt? (BM25-only vs hybrid)
2. What RRF k constant is optimal? (10/20/40/60/100)
3. Do boost layers improve ranking? (minimal vs defaults vs tuned)
4. What boost parameter values work best?

Usage::

    kairix eval hybrid-sweep \\
        --suite suites/v2-independent-gold.yaml \\
        --output hybrid-sweep-results.csv

Requires a running kairix instance with DB, embeddings, and optionally Neo4j.
"""

from __future__ import annotations

import csv
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kairix.eval.constants import CATEGORY_ALIASES, CATEGORY_WEIGHTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sweep configuration space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HybridSweepConfig:
    """A single configuration to evaluate in the sweep."""

    name: str
    mode: str  # "hybrid", "bm25_only", or "bm25_primary"
    rrf_k: int = 60
    entity_enabled: bool = True
    entity_factor: float = 0.20
    entity_cap: float = 2.0
    procedural_enabled: bool = True
    procedural_factor: float = 1.4
    chunk_date_enabled: bool = False
    bm25_limit: int = 20
    vec_limit: int = 10
    vec_distance_threshold: float = 0.0  # 0 = no filtering; >0 = discard vec results above this distance


def build_default_configs() -> list[HybridSweepConfig]:
    """Build the default sweep configuration space."""
    configs: list[HybridSweepConfig] = []

    # --- Baseline: BM25-only (current best = 0.749 NDCG) ---
    configs.append(
        HybridSweepConfig(
            name="bm25-only",
            mode="bm25_only",
        )
    )

    # --- RRF k sweep (hybrid, minimal boosts) ---
    for k in [10, 20, 40, 60, 100]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-k{k}-minimal",
                mode="hybrid",
                rrf_k=k,
                entity_enabled=False,
                procedural_enabled=False,
            )
        )

    # --- RRF k sweep (hybrid, default boosts) ---
    for k in [20, 40, 60]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-k{k}-defaults",
                mode="hybrid",
                rrf_k=k,
                entity_enabled=True,
                procedural_enabled=True,
            )
        )

    # --- Entity boost factor sweep (k=60) ---
    for ef in [0.10, 0.20, 0.30, 0.50]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-entity-f{ef}",
                mode="hybrid",
                rrf_k=60,
                entity_enabled=True,
                entity_factor=ef,
                procedural_enabled=True,
            )
        )

    # --- Entity cap sweep ---
    for cap in [1.5, 2.0, 3.0]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-entity-cap{cap}",
                mode="hybrid",
                rrf_k=60,
                entity_enabled=True,
                entity_cap=cap,
                procedural_enabled=True,
            )
        )

    # --- Procedural factor sweep ---
    for pf in [1.0, 1.2, 1.4, 1.6, 2.0]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-proc-f{pf}",
                mode="hybrid",
                rrf_k=60,
                entity_enabled=True,
                procedural_enabled=True,
                procedural_factor=pf,
            )
        )

    # --- BM25 limit sweep (more/fewer candidates for RRF) ---
    for lim in [10, 20, 30, 50]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-bm25lim{lim}",
                mode="hybrid",
                rrf_k=60,
                entity_enabled=False,
                procedural_enabled=False,
                bm25_limit=lim,
            )
        )

    # --- Vector limit sweep ---
    for vlim in [5, 10, 20, 30]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-vlim{vlim}",
                mode="hybrid",
                rrf_k=60,
                entity_enabled=False,
                procedural_enabled=False,
                vec_limit=vlim,
            )
        )

    # --- Distance-gated hybrid (filter poor vector results before RRF) ---
    for threshold in [0.3, 0.4, 0.5]:
        configs.append(
            HybridSweepConfig(
                name=f"hybrid-gate-d{threshold}",
                mode="hybrid",
                rrf_k=60,
                entity_enabled=False,
                procedural_enabled=False,
                vec_distance_threshold=threshold,
            )
        )

    # --- BM25-primary mode (BM25 ranked first, vector-only appended) ---
    for vlim in [5, 10, 20]:
        configs.append(
            HybridSweepConfig(
                name=f"bm25primary-v{vlim}",
                mode="bm25_primary",
                bm25_limit=20,
                vec_limit=vlim,
                entity_enabled=False,
                procedural_enabled=False,
            )
        )

    # BM25-primary with more BM25 candidates
    configs.append(
        HybridSweepConfig(
            name="bm25primary-bm30-v10",
            mode="bm25_primary",
            bm25_limit=30,
            vec_limit=10,
            entity_enabled=False,
            procedural_enabled=False,
        )
    )

    # --- Best combo candidates (informed by individual sweeps) ---
    configs.append(
        HybridSweepConfig(
            name="hybrid-tuned-a",
            mode="hybrid",
            rrf_k=20,
            entity_enabled=True,
            entity_factor=0.30,
            entity_cap=2.0,
            procedural_enabled=True,
            procedural_factor=1.4,
            bm25_limit=30,
            vec_limit=10,
        )
    )
    configs.append(
        HybridSweepConfig(
            name="hybrid-tuned-b",
            mode="hybrid",
            rrf_k=40,
            entity_enabled=True,
            entity_factor=0.20,
            entity_cap=2.0,
            procedural_enabled=True,
            procedural_factor=1.2,
            bm25_limit=20,
            vec_limit=20,
        )
    )

    return configs


# ---------------------------------------------------------------------------
# Metrics (reused from sweep.py with minor adaptations)
# ---------------------------------------------------------------------------


def _build_rel_map(gold: list[dict]) -> tuple[dict[str, int], str]:
    """Build relevance map from gold_titles or gold_paths format."""
    if not gold:
        return {}, "stem"
    if "title" in gold[0]:
        return {str(g["title"]).lower().strip(): int(g.get("relevance", 0)) for g in gold}, "stem"
    elif "path" in gold[0]:
        return {str(g["path"]).lower().strip(): int(g.get("relevance", 0)) for g in gold}, "path"
    return {}, "stem"


def _match_path(retrieved: str, rel_map: dict[str, int], mode: str) -> int:
    """Look up relevance grade for a retrieved path."""
    if mode == "stem":
        return rel_map.get(Path(retrieved).stem.lower(), 0)
    elif mode == "path":
        retrieved_lower = retrieved.lower()
        if retrieved_lower in rel_map:
            return rel_map[retrieved_lower]
        for gold_path, rel in rel_map.items():
            if retrieved_lower.endswith(gold_path) or gold_path.endswith(retrieved_lower):
                return rel
    return 0


def compute_ndcg(retrieved_paths: list[str], gold: list[dict], k: int = 10) -> float:
    """Compute NDCG@k for a single query."""
    if not gold:
        return 0.0
    rel_map, mode = _build_rel_map(gold)
    dcg = 0.0
    for i, path in enumerate(retrieved_paths[:k]):
        rel = _match_path(path, rel_map, mode)
        dcg += rel / math.log2(i + 2)
    ideal_rels = sorted(rel_map.values(), reverse=True)[:k]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal_rels))
    return dcg / idcg if idcg > 0 else 0.0


def compute_hit_at_k(retrieved_paths: list[str], gold: list[dict], k: int = 5) -> bool:
    """Check if any relevant gold doc appears in top-k."""
    rel_map, mode = _build_rel_map(gold)
    for path in retrieved_paths[:k]:
        if _match_path(path, rel_map, mode) >= 1:
            return True
    return False


def compute_mrr(retrieved_paths: list[str], gold: list[dict], k: int = 10) -> float:
    """Compute MRR@k (reciprocal rank of first relevant doc)."""
    rel_map, mode = _build_rel_map(gold)
    for i, path in enumerate(retrieved_paths[:k]):
        if _match_path(path, rel_map, mode) >= 1:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class HybridSweepResult:
    """Result of evaluating a single hybrid configuration."""

    config: HybridSweepConfig
    weighted_total: float = 0.0
    ndcg_at_10: float = 0.0
    hit_at_5: float = 0.0
    mrr_at_10: float = 0.0
    category_scores: dict[str, float] = field(default_factory=dict)
    n_cases: int = 0
    n_vec_failed: int = 0
    avg_bm25_count: float = 0.0
    avg_vec_count: float = 0.0
    avg_fused_count: float = 0.0
    avg_latency_ms: float = 0.0
    duration_s: float = 0.0


@dataclass
class HybridSweepReport:
    """Summary of a full hybrid calibration sweep."""

    results: list[HybridSweepResult] = field(default_factory=list)
    best: HybridSweepResult | None = None
    total_configs: int = 0
    total_duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Embedding cache (avoids redundant API calls across configs)
# ---------------------------------------------------------------------------

_embedding_cache: dict[str, bytes | None] = {}


def _get_embedding_cached(query: str) -> bytes | None:
    """Get embedding bytes for a query, caching across configs."""
    if query not in _embedding_cache:
        from kairix.search.hybrid import embed_text_as_bytes

        _embedding_cache[query] = embed_text_as_bytes(query)
    return _embedding_cache[query]


def _clear_embedding_cache() -> None:
    """Clear the embedding cache."""
    _embedding_cache.clear()


# ---------------------------------------------------------------------------
# Retrieval functions (per-config)
# ---------------------------------------------------------------------------


def _retrieve_bm25_only(
    query: str,
    collections: list[str] | None,
    limit: int = 20,
) -> tuple[list[str], dict[str, Any]]:
    """BM25-only retrieval. Returns (paths, metadata)."""
    from kairix.search.bm25 import bm25_search

    results = bm25_search(query=query, collections=collections, limit=limit)
    paths = [r["file"] for r in results]
    return paths, {"bm25_count": len(results), "vec_count": 0, "fused_count": len(results)}


def _retrieve_hybrid(
    query: str,
    collections: list[str] | None,
    config: HybridSweepConfig,
) -> tuple[list[str], dict[str, Any]]:
    """
    Full hybrid retrieval with configurable parameters.

    Runs BM25 + vector in parallel, fuses with RRF at the specified k,
    then applies boost layers per config.
    """
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    from kairix.db import get_db_path, load_extensions
    from kairix.search.bm25 import BM25Result, bm25_search
    from kairix.search.config import (
        EntityBoostConfig,
        ProceduralBoostConfig,
    )
    from kairix.search.intent import QueryIntent, classify
    from kairix.search.rrf import (
        entity_boost_neo4j,
        procedural_boost,
        rrf,
    )
    from kairix.search.vector import VecResult, vector_search_bytes

    intent = classify(query)
    bm25_results: list[BM25Result] = []
    vec_results: list[VecResult] = []
    vec_failed = False

    # Parallel dispatch
    with ThreadPoolExecutor(max_workers=2) as executor:
        bm25_fut = executor.submit(
            bm25_search,
            query,
            collections,
            config.bm25_limit,
        )

        def _vec_search() -> list[VecResult]:
            try:
                query_bytes = _get_embedding_cached(query)
                if not query_bytes:
                    return []
                db_path = get_db_path()
                db = sqlite3.connect(str(db_path))
                try:
                    load_extensions(db)
                    return vector_search_bytes(db, query_bytes, k=config.vec_limit, collections=collections)
                finally:
                    db.close()
            except Exception as e:
                logger.debug("hybrid_sweep: vec search failed — %s", e)
                return []

        vec_fut = executor.submit(_vec_search)

        try:
            bm25_results = bm25_fut.result(timeout=10) or []
        except Exception:
            bm25_results = []

        try:
            vec_results = vec_fut.result(timeout=30) or []
        except Exception:
            vec_failed = True

    if not vec_results:
        vec_failed = True

    # Distance gating: filter out vector results above threshold
    if config.vec_distance_threshold > 0 and vec_results:
        vec_results = [v for v in vec_results if v["distance"] <= config.vec_distance_threshold]

    # RRF fusion with configurable k
    fused = rrf(bm25_results, vec_results, k=config.rrf_k)

    # Entity boost
    if config.entity_enabled:
        try:
            from kairix.graph.client import get_client as _get_neo4j

            neo4j_client = _get_neo4j()
            entity_cfg = EntityBoostConfig(
                enabled=True,
                factor=config.entity_factor,
                cap=config.entity_cap,
            )
            fused = entity_boost_neo4j(fused, neo4j_client, config=entity_cfg)
        except Exception as e:
            logger.debug("hybrid_sweep: entity boost failed — %s", e)

    # Procedural boost (only for PROCEDURAL intent)
    if config.procedural_enabled and intent == QueryIntent.PROCEDURAL:
        proc_cfg = ProceduralBoostConfig(
            enabled=True,
            factor=config.procedural_factor,
        )
        fused = procedural_boost(fused, config=proc_cfg)

    paths = [r.path for r in fused]
    meta = {
        "bm25_count": len(bm25_results),
        "vec_count": len(vec_results),
        "fused_count": len(fused),
        "vec_failed": vec_failed,
        "intent": intent.value,
    }
    return paths, meta


def _retrieve_bm25_primary(
    query: str,
    collections: list[str] | None,
    config: HybridSweepConfig,
) -> tuple[list[str], dict[str, Any]]:
    """
    BM25-primary retrieval: BM25 results first (in BM25 rank order),
    then vector-only results appended at the bottom.

    This preserves BM25's strong NDCG while gaining vector's recall advantage.
    Deduplicates vector results that already appear in BM25.
    """
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    from kairix.db import get_db_path, load_extensions
    from kairix.search.bm25 import bm25_search
    from kairix.search.vector import vector_search_bytes

    bm25_results = []
    vec_results: list[Any] = []
    vec_failed = False

    with ThreadPoolExecutor(max_workers=2) as executor:
        bm25_fut = executor.submit(bm25_search, query, collections, config.bm25_limit)

        def _vec_search() -> list[Any]:
            try:
                query_bytes = _get_embedding_cached(query)
                if not query_bytes:
                    return []
                db_path = get_db_path()
                db = sqlite3.connect(str(db_path))
                try:
                    load_extensions(db)
                    return vector_search_bytes(db, query_bytes, k=config.vec_limit, collections=collections)
                finally:
                    db.close()
            except Exception:
                return []

        vec_fut = executor.submit(_vec_search)

        try:
            bm25_results = bm25_fut.result(timeout=10) or []
        except Exception as e:
            logger.debug("bm25_primary: BM25 failed — %s", e)
        try:
            vec_results = vec_fut.result(timeout=30) or []
        except Exception:
            vec_failed = True

    if not vec_results:
        vec_failed = True

    # BM25 paths first (in rank order)
    bm25_paths = [r["file"] for r in bm25_results]
    seen = set(p.lower() for p in bm25_paths)

    # Append vector-only results (not already in BM25) at the end
    vec_only_paths = []
    for vr in vec_results:
        if vr["path"].lower() not in seen:
            vec_only_paths.append(vr["path"])
            seen.add(vr["path"].lower())

    paths = bm25_paths + vec_only_paths
    meta = {
        "bm25_count": len(bm25_results),
        "vec_count": len(vec_results),
        "fused_count": len(paths),
        "vec_failed": vec_failed,
        "vec_only_appended": len(vec_only_paths),
    }
    return paths, meta


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------


def sweep_hybrid_params(
    suite_path: Path,
    output_path: Path | None = None,
    configs: list[HybridSweepConfig] | None = None,
    collections_override: list[str] | None = None,
) -> HybridSweepReport:
    """
    Grid search over hybrid pipeline configurations.

    For each configuration, runs all suite queries through the full pipeline
    (or BM25-only) and computes NDCG@10, Hit@5, MRR@10 against the gold suite.

    Args:
        suite_path:            Path to benchmark suite YAML (independent gold).
        output_path:           Optional CSV output for results.
        configs:               Configurations to evaluate. Defaults to build_default_configs().
        collections_override:  Explicit collection list. Overrides suite metadata when set.

    Returns:
        HybridSweepReport with results sorted by weighted_total descending.
    """
    if configs is None:
        configs = build_default_configs()

    with open(suite_path) as f:
        suite_data = yaml.safe_load(f)

    cases = suite_data.get("cases", [])
    if not cases:
        logger.error("hybrid_sweep: no cases in suite %s", suite_path)
        return HybridSweepReport()

    ndcg_cases = [c for c in cases if c.get("score_method") == "ndcg" and (c.get("gold_titles") or c.get("gold_paths"))]
    if not ndcg_cases:
        logger.error("hybrid_sweep: no ndcg-scored cases with gold in suite")
        return HybridSweepReport()

    logger.info(
        "hybrid_sweep: %d configs x %d cases = %d evaluations",
        len(configs),
        len(ndcg_cases),
        len(configs) * len(ndcg_cases),
    )

    # Determine collections: CLI override > suite metadata > None (all)
    collections = collections_override or suite_data.get("collections")

    report = HybridSweepReport()
    report.total_configs = len(configs)
    t_total_start = time.monotonic()

    for cfg in configs:
        t_start = time.monotonic()

        ndcg_scores: list[float] = []
        hit_scores: list[bool] = []
        mrr_scores: list[float] = []
        category_ndcg: dict[str, list[float]] = {}
        total_bm25 = 0
        total_vec = 0
        total_fused = 0
        total_latency = 0.0
        n_vec_failed = 0

        for case in ndcg_cases:
            query = case["query"]
            gold = case.get("gold_titles") or case.get("gold_paths", [])
            raw_category = case.get("category", "recall")
            category = CATEGORY_ALIASES.get(raw_category, raw_category)

            t_q_start = time.monotonic()

            if cfg.mode == "bm25_only":
                paths, meta = _retrieve_bm25_only(query, collections, limit=cfg.bm25_limit)
            elif cfg.mode == "bm25_primary":
                paths, meta = _retrieve_bm25_primary(query, collections, cfg)
            else:
                paths, meta = _retrieve_hybrid(query, collections, cfg)

            t_q_end = time.monotonic()
            total_latency += (t_q_end - t_q_start) * 1000.0

            total_bm25 += meta.get("bm25_count", 0)
            total_vec += meta.get("vec_count", 0)
            total_fused += meta.get("fused_count", 0)
            if meta.get("vec_failed"):
                n_vec_failed += 1

            ndcg = compute_ndcg(paths, gold)
            hit = compute_hit_at_k(paths, gold)
            mrr = compute_mrr(paths, gold)

            ndcg_scores.append(ndcg)
            hit_scores.append(hit)
            mrr_scores.append(mrr)

            if category not in category_ndcg:
                category_ndcg[category] = []
            category_ndcg[category].append(ndcg)

        n = len(ndcg_cases)
        cat_scores = {cat: sum(scores) / len(scores) if scores else 0.0 for cat, scores in category_ndcg.items()}
        weighted_total = sum(cat_scores.get(cat, 0.0) * weight for cat, weight in CATEGORY_WEIGHTS.items())

        result = HybridSweepResult(
            config=cfg,
            weighted_total=weighted_total,
            ndcg_at_10=sum(ndcg_scores) / n if n else 0.0,
            hit_at_5=sum(hit_scores) / n if n else 0.0,
            mrr_at_10=sum(mrr_scores) / n if n else 0.0,
            category_scores=cat_scores,
            n_cases=n,
            n_vec_failed=n_vec_failed,
            avg_bm25_count=total_bm25 / n if n else 0.0,
            avg_vec_count=total_vec / n if n else 0.0,
            avg_fused_count=total_fused / n if n else 0.0,
            avg_latency_ms=total_latency / n if n else 0.0,
            duration_s=time.monotonic() - t_start,
        )
        report.results.append(result)

        logger.info(
            "hybrid_sweep: %-30s → weighted=%.4f NDCG=%.4f Hit@5=%.3f vec_fail=%d latency=%.0fms (%ds)",
            cfg.name,
            weighted_total,
            result.ndcg_at_10,
            result.hit_at_5,
            n_vec_failed,
            result.avg_latency_ms,
            int(result.duration_s),
        )

    # Sort by weighted total
    report.results.sort(key=lambda r: r.weighted_total, reverse=True)
    report.best = report.results[0] if report.results else None
    report.total_duration_s = time.monotonic() - t_total_start

    # Write CSV
    if output_path and report.results:
        _write_csv(output_path, report)

    # Clean up embedding cache
    _clear_embedding_cache()

    # Print summary table
    _print_summary(report)

    return report


def _write_csv(output_path: Path, report: HybridSweepReport) -> None:
    """Write sweep results to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cat_names = sorted(CATEGORY_WEIGHTS.keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "config_name",
                "mode",
                "rrf_k",
                "entity_enabled",
                "entity_factor",
                "entity_cap",
                "proc_enabled",
                "proc_factor",
                "bm25_limit",
                "vec_limit",
                "weighted_total",
                "ndcg_at_10",
                "hit_at_5",
                "mrr_at_10",
                *cat_names,
                "n_vec_failed",
                "avg_bm25",
                "avg_vec",
                "avg_fused",
                "avg_latency_ms",
                "duration_s",
            ]
        )
        for r in report.results:
            c = r.config
            writer.writerow(
                [
                    c.name,
                    c.mode,
                    c.rrf_k,
                    c.entity_enabled,
                    c.entity_factor,
                    c.entity_cap,
                    c.procedural_enabled,
                    c.procedural_factor,
                    c.bm25_limit,
                    c.vec_limit,
                    f"{r.weighted_total:.4f}",
                    f"{r.ndcg_at_10:.4f}",
                    f"{r.hit_at_5:.4f}",
                    f"{r.mrr_at_10:.4f}",
                    *[f"{r.category_scores.get(cat, 0):.4f}" for cat in cat_names],
                    r.n_vec_failed,
                    f"{r.avg_bm25_count:.1f}",
                    f"{r.avg_vec_count:.1f}",
                    f"{r.avg_fused_count:.1f}",
                    f"{r.avg_latency_ms:.0f}",
                    f"{r.duration_s:.1f}",
                ]
            )


def _print_summary(report: HybridSweepReport) -> None:
    """Print a formatted summary table to the logger."""
    if not report.results:
        return

    logger.info("")
    logger.info("=" * 100)
    logger.info("HYBRID CALIBRATION SWEEP — %d configs, %.0fs total", report.total_configs, report.total_duration_s)
    logger.info("=" * 100)
    logger.info(
        "%-30s  %6s  %6s  %6s  %6s  %5s  %6s",
        "Config",
        "Weight",
        "NDCG",
        "Hit@5",
        "MRR",
        "VecF",
        "Lat ms",
    )
    logger.info("-" * 100)

    for r in report.results[:20]:  # Top 20
        logger.info(
            "%-30s  %6.4f  %6.4f  %6.3f  %6.4f  %5d  %6.0f",
            r.config.name,
            r.weighted_total,
            r.ndcg_at_10,
            r.hit_at_5,
            r.mrr_at_10,
            r.n_vec_failed,
            r.avg_latency_ms,
        )

    if report.best:
        logger.info("-" * 100)
        b = report.best
        logger.info("BEST: %s", b.config.name)
        logger.info(
            "  Mode: %s | RRF k=%d | Entity: %s (f=%.2f, cap=%.1f) | Proc: %s (f=%.1f)",
            b.config.mode,
            b.config.rrf_k,
            b.config.entity_enabled,
            b.config.entity_factor,
            b.config.entity_cap,
            b.config.procedural_enabled,
            b.config.procedural_factor,
        )
        logger.info(
            "  Weighted=%.4f  NDCG=%.4f  Hit@5=%.3f  MRR=%.4f", b.weighted_total, b.ndcg_at_10, b.hit_at_5, b.mrr_at_10
        )
        logger.info(
            "  Categories: %s",
            "  ".join(
                f"{cat}={b.category_scores.get(cat, 0):.3f}"
                for cat in sorted(CATEGORY_WEIGHTS.keys())
                if CATEGORY_WEIGHTS[cat] > 0
            ),
        )
    logger.info("=" * 100)
