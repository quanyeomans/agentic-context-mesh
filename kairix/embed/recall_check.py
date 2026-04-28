"""
Post-embed recall quality gate.

Runs recall queries against the usearch vector index to detect silent
degradation (wrong dims, corrupt vectors, missing documents).
Writes results to ~/.cache/kairix/recall-check.json.
Alerts if score drops >10% from previous run.

Adaptive mode: when no RECALL_QUERIES env var is set, derives recall
queries from the indexed documents themselves (random sample of titles).
"""

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from kairix.embed.schema import EMBED_VECTOR_DIMS as EMBED_DIMS

logger = logging.getLogger(__name__)

RECALL_LOG = Path.home() / ".cache" / "kairix" / "recall-check.json"

# Fallback recall queries when no indexed documents exist and no env var is set.
# Each tuple: (id, query, expected_title_fragment).
DEFAULT_RECALL_QUERIES = [
    ("R01", "architecture decision record", "architecture"),
    ("R02", "how to deploy", "deploy"),
    ("R03", "testing strategy", "test"),
    ("R04", "search query", "search"),
    ("R05", "project documentation", "project"),
]

DEGRADATION_THRESHOLD = 0.10  # alert if score drops more than 10%
RECALL_LIMIT = 5  # top-k results to check for gold hit
ADAPTIVE_SAMPLE_SIZE = 5  # number of documents to sample for adaptive queries


def _get_recall_queries(db: sqlite3.Connection | None = None) -> list[tuple]:
    """Return recall queries — from env var, adaptive corpus sample, or defaults.

    Priority:
      1. RECALL_QUERIES env var (JSON array of [id, query, expected_fragment])
      2. Adaptive: random sample of indexed document titles
      3. Static defaults
    """
    env = os.environ.get("RECALL_QUERIES")
    if env:
        try:
            return [tuple(q) for q in json.loads(env)]
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("RECALL_QUERIES env var is not valid JSON — trying adaptive")

    # Adaptive: sample titles from the index
    if db is not None:
        adaptive = _build_adaptive_queries(db)
        if adaptive:
            return adaptive

    return DEFAULT_RECALL_QUERIES


def _build_adaptive_queries(db: sqlite3.Connection) -> list[tuple]:
    """Build recall queries from a random sample of indexed document titles."""
    try:
        rows = db.execute(
            """
            SELECT path, title FROM documents
            WHERE active = 1 AND title IS NOT NULL AND title != ''
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (ADAPTIVE_SAMPLE_SIZE,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    if not rows:
        return []

    queries = []
    for i, (path, title) in enumerate(rows, 1):
        # Use the title as the query, path stem as the gold fragment
        readable = title.replace("-", " ").replace("_", " ")
        stem = Path(path).stem.lower()
        queries.append((f"A{i:02d}", readable, stem))

    return queries


def _embed_query(query: str) -> np.ndarray | None:
    """Embed a single query string via Azure OpenAI. Returns float32 numpy array or None."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")

    if not api_key or not endpoint:
        logger.warning("Azure credentials not set — skipping recall check")
        return None

    try:
        import requests

        resp = requests.post(
            f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2024-02-01",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={"input": [query], "dimensions": EMBED_DIMS},
            timeout=30,
        )
        resp.raise_for_status()
        vec = resp.json()["data"][0]["embedding"]
        arr = np.array(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr /= norm
        return arr
    except Exception as e:
        logger.warning("Recall embed failed for query '%s': %s", query[:40], e)
        return None


def _vsearch_usearch(query_vec: np.ndarray, limit: int = RECALL_LIMIT) -> list[str]:
    """Run vector similarity search via usearch index.

    Returns list of document paths in similarity order.
    """
    try:
        from kairix.search.hybrid import _get_vector_index

        index = _get_vector_index()
        if index is None:
            logger.warning("usearch index not available for recall check")
            return []

        results = index.search(query_vec, k=limit)
        return [r["path"] for r in results]
    except Exception as e:
        logger.warning("usearch recall search failed: %s", e)
        return []


def check_recall(db: sqlite3.Connection | None = None) -> dict:
    """
    Run recall check queries via usearch vector search.
    Returns {score, passed, total, detail}.
    Score is fraction of queries where gold path fragment appears in top-5.

    If db is None, opens the kairix DB internally (for adaptive query building).
    """
    close_db = False
    if db is None:
        try:
            from kairix.db import get_db_path

            db = sqlite3.connect(str(get_db_path()))
            close_db = True
        except FileNotFoundError:
            db = None

    queries = _get_recall_queries(db)
    passed = 0
    detail = []

    for qid, query, gold_fragment in queries:
        query_vec = _embed_query(query)
        if query_vec is None:
            detail.append(
                {
                    "id": qid,
                    "query": query,
                    "gold_fragment": gold_fragment,
                    "hit": False,
                    "returned": [],
                    "skipped": True,
                }
            )
            continue

        files = _vsearch_usearch(query_vec)
        hit = any(gold_fragment.lower() in f.lower() for f in files)
        if hit:
            passed += 1
        detail.append(
            {
                "id": qid,
                "query": query,
                "gold_fragment": gold_fragment,
                "hit": hit,
                "returned": files,
                "skipped": False,
            }
        )

    if close_db and db is not None:
        db.close()

    checked = sum(1 for d in detail if not d.get("skipped"))
    score = passed / checked if checked > 0 else 0.0
    return {
        "score": round(score, 4),
        "passed": passed,
        "total": checked,
        "timestamp": int(time.time()),
        "detail": detail,
    }


def load_previous_score() -> float | None:
    """Load the most recent recall score from the log."""
    if not RECALL_LOG.exists():
        return None
    try:
        runs = json.loads(RECALL_LOG.read_text())
        if runs:
            return float(runs[-1].get("score", 0.0))
    except (json.JSONDecodeError, OSError, KeyError):
        pass
    return None


def save_recall_result(result: dict) -> None:
    """Append recall result to the log. Keep last 90 entries."""
    runs = []
    if RECALL_LOG.exists():
        try:
            runs = json.loads(RECALL_LOG.read_text())
        except (json.JSONDecodeError, OSError):
            runs = []
    runs.append(result)
    runs = runs[-90:]
    RECALL_LOG.write_text(json.dumps(runs, indent=2))


def run_recall_gate(alert_callback: Callable[[str], None] | None = None) -> tuple[bool, dict]:
    """
    Run the recall gate. Returns (passed, result_dict).

    If score dropped >DEGRADATION_THRESHOLD vs previous run, calls alert_callback(message).
    The alert_callback is injected by the CLI (e.g. Telegram notify).
    """
    result = check_recall()
    prev_score = load_previous_score()
    save_recall_result(result)

    score = result["score"]
    logger.info("Recall check: %d/%d (%.0f%%)", result["passed"], result["total"], score * 100)

    if prev_score is not None:
        delta = score - prev_score
        if delta < -DEGRADATION_THRESHOLD:
            msg = (
                f"Recall degraded: {score:.0%} (was {prev_score:.0%}, delta {delta:+.0%}). "
                "Check azure-embed.log and run kairix onboard check."
            )
            logger.warning(msg)
            if alert_callback:
                alert_callback(msg)
            return False, result

    return True, result
