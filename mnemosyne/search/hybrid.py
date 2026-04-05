"""
Hybrid search orchestrator for the Mnemosyne pipeline.

Orchestrates the full search pipeline:
  1. Classify query intent
  2. Dispatch BM25 and vector search in parallel (ThreadPoolExecutor)
  3. Fuse results with RRF
  4. Apply entity boosting (if entities.db available)
  5. Apply token budget
  6. Log retrieval event

Falls back to BM25-only if vector search fails.
Logs every search event to /data/mnemosyne/logs/search.jsonl.

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

from mnemosyne._azure import embed_text_as_bytes
from mnemosyne.embed.schema import get_qmd_db_path, load_sqlite_vec
from mnemosyne.search.bm25 import BM25Result, bm25_search
from mnemosyne.search.budget import BudgetedResult, apply_budget
from mnemosyne.search.intent import QueryIntent, classify
from mnemosyne.search.rrf import FusedResult, entity_boost, rrf
from mnemosyne.search.vector import VecResult, vector_search_bytes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEARCH_LOG_PATH = Path(os.environ.get("MNEMOSYNE_SEARCH_LOG", "/data/mnemosyne/logs/search.jsonl"))
ENTITIES_DB_PATH = Path(os.environ.get("MNEMOSYNE_ENTITIES_DB", "/data/mnemosyne/entities.db"))

# Query logging (privacy-sensitive — disabled by default)
_LOG_QUERIES: bool = os.getenv("MNEMOSYNE_LOG_QUERIES", "0") == "1"
_QUERY_LOG_PATH: Path = Path(os.getenv("MNEMOSYNE_QUERY_LOG", "/data/mnemosyne/logs/queries.jsonl"))

# Rotate when file exceeds this size
_QUERY_LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB

# Collections used per intent/scope
# Collections available to all agents (shared knowledge + main vault)
_SHARED_COLLECTIONS = [
    "vault-projects",        # Active projects (01-Projects)
    "vault-areas",           # Areas of responsibility (02-Areas)
    "vault-resources",       # Reference material (03-Resources)
    "vault-agent-knowledge", # Agent knowledge files (04-Agent-Knowledge)
    "vault-knowledge",       # Generalised knowledge (05-Knowledge)
    "vault-entities",        # Entity knowledge graph — boosted for entity/person/org queries
    "knowledge-shared",
    "tc-engineering",
    "tc-agent-zone",
    # vault-archive excluded from default — search explicitly when historical context needed
]
# Per-agent private collections (agent name substituted at query time)
_AGENT_COLLECTIONS_TMPL = ["{agent}-memory"]  # knowledge-{agent} removed: vault-agent-knowledge already covers these files with correct prefixed paths


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
    fallback_used: bool = False  # True when KEYWORD/PROCEDURAL fell back to vector
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


def _open_entities_db() -> sqlite3.Connection | None:
    """Open entities.db. Returns None if unavailable."""
    try:
        if not ENTITIES_DB_PATH.exists():
            return None
        return sqlite3.connect(str(ENTITIES_DB_PATH))
    except Exception as e:
        logger.debug("hybrid: cannot open entities.db — %s", e)
        return None


# ---------------------------------------------------------------------------
# Search pipeline
# ---------------------------------------------------------------------------


def search(
    query: str,
    agent: str | None = None,
    scope: str = "shared+agent",
    budget: int = 3000,
) -> SearchResult:
    """
    Run the full hybrid search pipeline.

    Pipeline:
      1. Classify intent
      2. Determine collections
      3. Dispatch BM25 + vector in parallel (ThreadPoolExecutor)
      4. Fuse with RRF
      5. Entity boost (if entities.db available)
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
    if intent == QueryIntent.TEMPORAL:
        try:
            from datetime import date as _date

            from mnemosyne.temporal.index import query_temporal_chunks
            from mnemosyne.temporal.rewriter import extract_time_window, rewrite_temporal_query

            active_query = rewrite_temporal_query(query, reference_date=_date.today())
            start, end = extract_time_window(query, reference_date=_date.today())
            temporal_chunks = query_temporal_chunks(topic=active_query, start=start, end=end, limit=10)
        except Exception as _e:
            logger.warning("hybrid: temporal rewriting failed — %s", _e)
            active_query = query

    # For KEYWORD and PROCEDURAL queries, skip vector search
    skip_vector = intent in (QueryIntent.KEYWORD,)  # PROCEDURAL now runs hybrid like SEMANTIC

    # Dispatch BM25 and (optionally) vector search in parallel
    # For TEMPORAL intent, active_query is the rewritten (date-expanded) query
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures: dict[str, Future] = {}

        # Always run BM25
        futures["bm25"] = executor.submit(bm25_search, active_query, collections)

        # Run vector search unless intent says skip
        if not skip_vector:
            futures["vec"] = executor.submit(_run_vector_search, active_query, collections)

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

    if not vec_results and not skip_vector:
        vec_failed = True
        logger.info("hybrid: vector search returned no results, using BM25 only (query=%r)", query[:60])

    # KEYWORD/PROCEDURAL fallback: if BM25 returned nothing, retry with vector search
    if not bm25_results and skip_vector:
        logger.info("hybrid: %s BM25 returned 0 results — falling back to vector (query=%r)", intent.value, query[:60])
        try:
            vec_results = _run_vector_search(active_query, collections)
            fallback_used = True
            if not vec_results:
                vec_failed = True
                logger.warning("hybrid: keyword vector fallback also returned 0 results (query=%r)", query[:60])
        except Exception as e:
            logger.warning("hybrid: keyword vector fallback failed — %s", e)
            vec_failed = True

    # Fuse results
    fused: list[FusedResult] = rrf(bm25_results, vec_results)

    # Entity boosting
    entities_db = _open_entities_db()
    if entities_db is not None:
        try:
            fused = entity_boost(fused, entities_db)
        finally:
            entities_db.close()

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
                temporal_fused.append(FusedResult(
                    path=tc.source_path,
                    collection="temporal",
                    title=title,
                    snippet=tc.text[:500].strip(),
                    rrf_score=0.82,
                    boosted_score=0.82,
                ))
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

    # Optional raw query log (privacy-sensitive — controlled by MNEMOSYNE_LOG_QUERIES)
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


def _run_vector_search(query: str, collections: list[str]) -> list[VecResult]:
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
            return vector_search_bytes(db, query_bytes, collections=collections or None)
        finally:
            db.close()
    except Exception as e:
        logger.warning("hybrid: _run_vector_search failed — %s", e)
        return []
