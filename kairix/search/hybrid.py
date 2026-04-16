"""
Hybrid search orchestrator for the kairix pipeline.

Orchestrates the full search pipeline:
  1. Classify query intent
  2. Dispatch BM25 and vector search in parallel (ThreadPoolExecutor)
  3. Fuse results with RRF
  4. Apply entity boosting via Neo4j graph
  5. Apply token budget
  6. Log retrieval event

Falls back to BM25-only if vector search fails.
Logs every search event to the path set by KAIRIX_SEARCH_LOG (default: /data/kairix/logs/search.jsonl).

Never raises — returns SearchResult with empty results on any failure.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from kairix.embed.schema import get_qmd_db_path, load_sqlite_vec
from kairix.graph.client import get_client as _get_neo4j
from kairix.llm import get_default_backend as _get_llm
from kairix.search.bm25 import BM25_DEFAULT_LIMIT, BM25Result, bm25_search
from kairix.search.budget import BudgetedResult, apply_budget
from kairix.search.intent import QueryIntent, classify
from kairix.search.rrf import FusedResult, entity_boost_neo4j, procedural_boost, rrf
from kairix.search.vector import VecResult, vector_search_bytes

logger = logging.getLogger(__name__)


def embed_text_as_bytes(text: str) -> bytes | None:
    """Embed text via the default LLM backend and return packed float32 bytes."""
    return _get_llm().embed_as_bytes(text)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEARCH_LOG_PATH = Path(os.environ.get("KAIRIX_SEARCH_LOG", "/data/kairix/logs/search.jsonl"))

# Query logging (privacy-sensitive — disabled by default)
_LOG_QUERIES: bool = os.getenv("KAIRIX_LOG_QUERIES", "0") == "1"
_QUERY_LOG_PATH: Path = Path(os.getenv("KAIRIX_QUERY_LOG", "/data/kairix/logs/queries.jsonl"))

# Rotate when file exceeds this size
_QUERY_LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB

# Collections used per intent/scope
# These collection names must match your QMD index.yml configuration.
# Collections available to all agents (shared knowledge + main vault)
_SHARED_COLLECTIONS = [
    "vault-projects",  # Active projects (01-Projects)
    "vault-areas",  # Areas of responsibility (02-Areas)
    "vault-resources",  # Reference material (03-Resources)
    "vault-agent-knowledge",  # Agent knowledge files (04-Agent-Knowledge)
    "vault-knowledge",  # Generalised knowledge (05-Knowledge)
    "vault-entities",  # Entity knowledge graph — boosted for entity/person/org queries
    "knowledge-shared",
    "tc-agent-zone",  # Engineering & ops docs: runbooks, architecture, deployment scripts
    "tc-engineering",  # Engineering standards and patterns
    # vault-archive excluded from default — search explicitly when historical context needed
    # Add deployment-specific collections via KAIRIX_EXTRA_COLLECTIONS (comma-separated):
    *[c.strip() for c in os.environ.get("KAIRIX_EXTRA_COLLECTIONS", "").split(",") if c.strip()],
]
# Per-agent private collections (agent name substituted at query time)
_AGENT_COLLECTIONS_TMPL = ["{agent}-memory"]  # knowledge-{agent} removed: vault-agent-knowledge covers these


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """Full result from the hybrid search pipeline."""

    query: str
    intent: QueryIntent
    results: list[BudgetedResult] = field(default_factory=list)

    # Diagnostic info
    bm25_count: int = 0
    vec_count: int = 0
    fused_count: int = 0
    collections: list[str] = field(default_factory=list)
    tiers_used: list[str] = field(default_factory=list)
    total_tokens: int = 0
    latency_ms: float = 0.0
    vec_failed: bool = False
    fallback_used: bool = False  # True when BM25 returned 0 and vector was the sole source
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collections_for(agent: str | None, scope: str) -> list[str]:
    """Derive QMD collection list from agent and scope parameters."""
    cols: list[str] = list(_SHARED_COLLECTIONS)
    if agent and "agent" in scope:
        for tmpl in _AGENT_COLLECTIONS_TMPL:
            cols.append(tmpl.format(agent=agent))
    return cols


def _log_search_event(event: dict) -> None:
    """Append a search event to the JSONL log. Creates directory if needed."""
    try:
        SEARCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SEARCH_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        logger.warning("hybrid: failed to write search log — %s", e)


def _rotate_query_log(path: Path) -> None:
    """Rotate path → path.1, removing any older rotated file. Simple size-based rotation."""
    rotated = Path(str(path) + ".1")
    if rotated.exists():
        rotated.unlink()
    shutil.move(str(path), str(rotated))


def _log_query_event(event: dict) -> None:
    """Append a query event to the query JSONL log. Rotates at 10 MB. No-op if logging disabled."""
    if not _LOG_QUERIES:
        return
    try:
        _QUERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _QUERY_LOG_PATH.exists() and _QUERY_LOG_PATH.stat().st_size >= _QUERY_LOG_MAX_BYTES:
            _rotate_query_log(_QUERY_LOG_PATH)
        with _QUERY_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        logger.warning("hybrid: failed to write query log — %s", e)


def _open_vec_db() -> sqlite3.Connection | None:
    """Open QMD DB with sqlite-vec extension loaded. Returns None on any failure."""
    try:
        db_path = get_qmd_db_path()
        db = sqlite3.connect(str(db_path))
        load_sqlite_vec(db)
        return db
    except Exception as e:
        logger.warning("hybrid: cannot open vec DB — %s", e)
        return None


# ---------------------------------------------------------------------------
# Search pipeline
# ---------------------------------------------------------------------------


def search(
    query: str,
    agent: str | None = None,
    scope: str = "shared+agent",
    budget: int = 3000,
    _no_multi_hop: bool = False,
) -> SearchResult:
    """
    Run the full hybrid search pipeline.

    Pipeline:
      1. Classify intent
      2. Determine collections
      3. Dispatch BM25 + vector in parallel (ThreadPoolExecutor)
      4. Fuse with RRF
      5. Entity boost via Neo4j graph
      6. Apply token budget
      7. Log event

    Falls back to BM25-only if vector search fails.

    Args:
        query:   Search query string.
        agent:   Agent name for collection scoping (e.g. "shape", "builder").
        scope:   Collection scope: "shared", "agent", or "shared+agent".
        budget:  Token budget cap. Default 3000.

    Returns:
        SearchResult with results and diagnostic metadata.
        Never raises.
    """
    t_start = time.monotonic()

    intent = classify(query)
    collections = _collections_for(agent, scope)

    bm25_results: list[BM25Result] = []
    vec_results: list[VecResult] = []
    vec_failed = False
    fallback_used = False

    # Temporal query rewriting — expand query with explicit date tokens before retrieval
    active_query = query
    temporal_chunks: list = []
    date_filter_paths: frozenset[str] | None = None  # TMP-2: set in TEMPORAL block below
    if intent == QueryIntent.TEMPORAL:
        try:
            from datetime import date as _date

            from kairix.temporal.index import query_temporal_chunks
            from kairix.temporal.rewriter import extract_time_window, rewrite_temporal_query

            active_query = rewrite_temporal_query(query, reference_date=_date.today())
            start, end = extract_time_window(query, reference_date=_date.today())
            temporal_chunks = query_temporal_chunks(topic=active_query, start=start, end=end, limit=10)
            # TMP-2: build date-filtered path set to restrict BM25 and vector results
            # Only for RELATIVE temporal expressions (last week, recently, yesterday).
            # NOT for absolute date references (March 2026, 2026-03-09) — those queries
            # are ABOUT a time period, not filtered by document creation date.
            if start is not None:
                from kairix.embed.schema import get_date_filtered_paths
                from kairix.temporal.rewriter import is_relative_temporal

                if is_relative_temporal(query):
                    _tmp2_db = sqlite3.connect(str(get_qmd_db_path()))
                    try:
                        _paths = get_date_filtered_paths(_tmp2_db, start, end)
                        if _paths:  # empty = no dated chunks yet; do not filter
                            date_filter_paths = _paths
                            logger.debug(
                                "hybrid: TMP-2 date filter active — %d paths in [%s, %s]",
                                len(_paths),
                                start,
                                end,
                            )
                    except Exception as _dfp_e:
                        logger.warning("hybrid: TMP-2 get_date_filtered_paths failed — %s", _dfp_e)
                    finally:
                        _tmp2_db.close()
        except Exception as _e:
            logger.warning("hybrid: temporal rewriting failed — %s", _e)
            active_query = query

    # Neo4j client — used by planner (entity context) and entity_boost
    neo4j_client = _get_neo4j()

    # Multi-hop: decompose and run sub-queries in parallel, then merge. (Phase 4B-2)
    if intent == QueryIntent.MULTI_HOP and not _no_multi_hop:
        try:
            from kairix.search.planner import QueryPlanner

            planner = QueryPlanner()
            sub_queries = planner.decompose(query, neo4j_client=neo4j_client)
            if True:
                logger.debug("hybrid: MULTI_HOP sub-queries: %s", sub_queries)

                def _hybrid_search_fn(sq: str) -> list:
                    # Run full hybrid (BM25+vector) per sub-query, bypassing MULTI_HOP
                    sub_result = search(sq, agent=agent, scope=scope, budget=budget, _no_multi_hop=True)
                    # Return BudgetedResult list — planner uses .result.path via getattr fallback
                    return sub_result.results

                fused_results = planner.retrieve_and_merge(
                    sub_queries,
                    _hybrid_search_fn,
                    top_k_per_sub=6,
                    final_top_k=8,
                )
                # fused_results are BudgetedResult objects from sub-query search() calls
                # Use r.result (FusedResult) directly — avoids re-copying large snippets
                # and lets apply_budget re-score tiers fresh for the merged result set.
                seen_paths: set[str] = set()
                fused_for_budget: list[FusedResult] = []
                for i, r in enumerate(fused_results):
                    if not hasattr(r, "result") or not r.result.path:
                        continue
                    if r.result.path in seen_paths:
                        continue
                    seen_paths.add(r.result.path)
                    # Re-score with RRF position for merged ordering.
                    # Truncate snippet to cap per-result budget use; apply_budget
                    # will fetch fresh content at the appropriate tier anyway.
                    from dataclasses import replace as _replace

                    snippet = r.result.snippet or ""
                    merged_fr = _replace(
                        r.result,
                        snippet=snippet[:600] if len(snippet) > 600 else snippet,
                        rrf_score=1.0 / (60 + i + 1),
                        boosted_score=1.0 / (60 + i + 1),
                    )
                    fused_for_budget.append(merged_fr)
                budgeted = apply_budget(fused_for_budget, budget=budget)
                total_tokens = sum(r.token_estimate for r in budgeted)
                tiers_used = sorted(set(r.tier for r in budgeted))
                t_end = time.monotonic()
                latency_ms = (t_end - t_start) * 1000.0
                query_hash = hashlib.sha256(query.encode()).hexdigest()[:12]
                result = SearchResult(
                    query=query,
                    intent=intent,
                    results=budgeted,
                    bm25_count=len(fused_results),
                    vec_count=0,
                    fused_count=len(fused_for_budget),
                    collections=collections,
                    tiers_used=tiers_used,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    vec_failed=False,
                    fallback_used=False,
                )
                _log_search_event(
                    {
                        "query_hash": query_hash,
                        "intent": intent.value,
                        "agent": agent,
                        "scope": scope,
                        "bm25_count": len(fused_results),
                        "vec_count": 0,
                        "fused_count": len(fused_for_budget),
                        "budget_tokens": total_tokens,
                        "latency_ms": latency_ms,
                        "sub_queries": len(sub_queries),
                    }
                )
                return result

        except Exception as _mh_e:
            logger.warning("hybrid: MULTI_HOP planner failed (%s) — falling back to SEMANTIC", _mh_e)
            intent = QueryIntent.SEMANTIC

    # All intents run BM25 + vector in parallel via RRF.
    # Previously KEYWORD ran BM25-only, which degraded NDCG@10 (0.439) because:
    # (a) BM25-only misses semantically-relevant docs for proper nouns/codes
    # (b) the vector-only fallback when BM25 returned 0 results halved RRF scores
    # Running hybrid gives KEYWORD queries both BM25 exact-match strength and
    # vector semantic coverage — the same approach that lifted PROCEDURAL from 0.389→0.564.

    # Dispatch BM25 and vector search in parallel for all intents
    # For TEMPORAL intent, active_query is the rewritten (date-expanded) query
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures: dict[str, Future] = {}

        futures["bm25"] = executor.submit(
            bm25_search, active_query, collections, BM25_DEFAULT_LIMIT, None, date_filter_paths
        )
        futures["vec"] = executor.submit(_run_vector_search, active_query, collections, date_filter_paths)

        # Collect results
        for name, future in futures.items():
            try:
                result = future.result(timeout=10)
                if name == "bm25":
                    bm25_results = result or []
                elif name == "vec":
                    vec_results = result or []
            except Exception as e:
                logger.warning("hybrid: %s search future failed — %s", name, e)
                if name == "vec":
                    vec_failed = True

    if not vec_results:
        vec_failed = True
        logger.info("hybrid: vector search returned no results, using BM25 only (query=%r)", query[:60])

    # Fuse results
    fused: list[FusedResult] = rrf(bm25_results, vec_results)

    # Entity boost via Neo4j — boosts entity canonical notes by graph in-degree
    try:
        fused = entity_boost_neo4j(fused, neo4j_client)
    except Exception as _eb_e:
        logger.warning("hybrid: entity_boost_neo4j failed — %s", _eb_e)

    # Procedural boosting for PROCEDURAL intent
    if intent == QueryIntent.PROCEDURAL:
        fused = procedural_boost(fused)

    # Merge temporal chunks into fused results for TEMPORAL intent.
    # Previously stored as _temporal_chunks side-channel; now merged into main
    # results so benchmark and callers see them. (Phase 4B-1 fix 2026-04-05)
    if temporal_chunks and intent == QueryIntent.TEMPORAL:
        seen_paths = {fr.path for fr in fused}
        temporal_fused = []
        for tc in temporal_chunks[:6]:
            if tc.source_path not in seen_paths:
                heading = tc.metadata.get("section_heading") or tc.metadata.get("status") or ""
                title = (heading + " — " + tc.source_path.split("/")[-1]) if heading else tc.source_path.split("/")[-1]
                temporal_fused.append(
                    FusedResult(
                        path=tc.source_path,
                        collection="temporal",
                        title=title,
                        snippet=tc.text[:500].strip(),
                        rrf_score=0.82,
                        boosted_score=0.82,
                    )
                )
                seen_paths.add(tc.source_path)
        if temporal_fused:
            fused = temporal_fused + fused
            logger.debug("hybrid: merged %d temporal chunks into results", len(temporal_fused))

    # Apply token budget
    budgeted = apply_budget(fused, budget=budget)

    # Compute diagnostics
    total_tokens = sum(r.token_estimate for r in budgeted)
    tiers_used = sorted(set(r.tier for r in budgeted))

    t_end = time.monotonic()
    latency_ms = (t_end - t_start) * 1000.0

    # Build result
    result = SearchResult(
        query=query,
        intent=intent,
        results=budgeted,
        bm25_count=len(bm25_results),
        vec_count=len(vec_results),
        fused_count=len(fused),
        collections=collections,
        tiers_used=tiers_used,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        vec_failed=vec_failed,
        fallback_used=fallback_used,
    )

    # Attach temporal chunks for TEMPORAL intent (accessible to callers)
    if temporal_chunks:
        result.error = ""  # ensure no error state masks chunks
        if hasattr(result, "__slots__"):
            object.__setattr__(result, "_temporal_chunks", temporal_chunks)
        else:
            result._temporal_chunks = temporal_chunks  # type: ignore[attr-defined]

    # Log event
    query_hash = hashlib.sha256(query.encode()).hexdigest()[:12]
    _log_search_event(
        {
            "query_hash": query_hash,
            "intent": intent.value,
            "agent": agent,
            "scope": scope,
            "bm25_count": len(bm25_results),
            "vec_count": len(vec_results),
            "fused_count": len(fused),
            "collections": collections,
            "tiers_used": tiers_used,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 1),
            "vec_failed": vec_failed,
            "fallback_used": fallback_used,
            "ts": int(time.time()),
        }
    )

    # Optional raw query log (privacy-sensitive — controlled by KAIRIX_LOG_QUERIES)
    _log_query_event(
        {
            "ts": int(time.time()),
            "query": query,
            "query_hash": query_hash,
            "intent": intent.value,
            "agent": agent,
            "fused_count": len(fused),
            "vec_failed": vec_failed,
            "latency_ms": round(latency_ms, 1),
            "top_paths": [r.result.path for r in budgeted[:3]],
        }
    )

    return result


def _run_vector_search(
    query: str,
    collections: list[str],
    date_filter_paths: frozenset[str] | None = None,
) -> list[VecResult]:
    """
    Embed query and run vector search. Returns [] on any failure.
    Called from ThreadPoolExecutor — must not raise.

    Retries embed once on empty result to handle cold Key Vault fetches
    (first call may time out while KV secrets are being fetched; lru_cache
    means the second call uses the warm cache and succeeds).
    """
    import time as _time

    try:
        query_bytes = embed_text_as_bytes(query)
        if not query_bytes:
            # Retry once — cold KV start may have timed out on first call
            logger.warning("hybrid: embed returned empty on first try — retrying once (query=%r)", query[:60])
            _time.sleep(0.5)
            query_bytes = embed_text_as_bytes(query)
        if not query_bytes:
            logger.warning("hybrid: embed returned empty after retry — skipping vector search")
            return []

        db = _open_vec_db()
        if db is None:
            return []

        try:
            return vector_search_bytes(
                db, query_bytes, collections=collections or None, date_filter_paths=date_filter_paths
            )
        finally:
            db.close()
    except Exception as e:
        logger.warning("hybrid: _run_vector_search failed — %s", e)
        return []
