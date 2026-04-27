"""
Post-embed recall quality gate.

Runs recall queries via direct SQLite vector search (bypasses legacy vsearch,
which loads a local llama.cpp model and hangs on CPU-only deployments).

Embeds each query via Azure OpenAI, then queries vectors_vec directly.
Detects silent degradation (wrong dims, corrupt vectors, schema mismatch).
Writes results to ~/.cache/kairix/recall-check.json.
Alerts if score drops >10% from previous run.
"""

import json
import logging
import os
import sqlite3
import struct
import time
from collections.abc import Callable
from pathlib import Path

import requests

from kairix.embed.schema import EMBED_VECTOR_DIMS as EMBED_DIMS

logger = logging.getLogger(__name__)

RECALL_LOG = Path.home() / ".cache" / "kairix" / "recall-check.json"

# Recall queries with known gold path fragments (must appear in top-5 results)
# Path fragments are vault-relative and lowercase-matched
# Override with your own vault's known documents via RECALL_QUERIES env var (JSON)
DEFAULT_RECALL_QUERIES = [
    ("R01", "Arize Phoenix observability recommendation", "arize-observability-research"),
    ("R03", "kairix hybrid search architecture", "kairix"),
    ("R04", "how to run benchmark evaluation", "benchmark"),
    ("R05", "engineering standards and patterns", "facts"),
    ("R12", "NemoClaw analysis OpenShell verdict", "nemoclaw-analysis"),
]

DEGRADATION_THRESHOLD = 0.10  # alert if score drops more than 10%
RECALL_LIMIT = 5  # top-k results to check for gold hit


def _get_recall_queries() -> list[tuple]:
    """Return recall queries — from env var override or defaults."""
    env = os.environ.get("RECALL_QUERIES")
    if env:
        try:
            return [tuple(q) for q in json.loads(env)]
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("RECALL_QUERIES env var is not valid JSON — using defaults")
    return DEFAULT_RECALL_QUERIES


def _embed_query(query: str) -> bytes | None:
    """Embed a single query string via Azure OpenAI. Returns packed float32 bytes or None."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")

    if not api_key or not endpoint:
        logger.warning("Azure credentials not set — skipping recall check")
        return None

    try:
        resp = requests.post(
            f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2024-02-01",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={"input": [query], "dimensions": EMBED_DIMS},
            timeout=30,
        )
        resp.raise_for_status()
        vec = resp.json()["data"][0]["embedding"]
        return struct.pack(f"<{len(vec)}f", *vec)
    except Exception as e:
        logger.warning(f"Recall embed failed for query '{query[:40]}': {e}")
        return None


def _vsearch_direct(db: sqlite3.Connection, query_vec: bytes, limit: int = RECALL_LIMIT) -> list[str]:
    """
    Run vector similarity search directly against vectors_vec.
    Bypasses legacy vsearch (which loads llama.cpp and hangs on CPU-only VMs).
    Returns list of document paths in similarity order.
    """
    try:
        rows = db.execute(
            """
            SELECT d.path
            FROM vectors_vec vv
            JOIN content_vectors cv ON (cv.hash || '_' || cv.seq) = vv.hash_seq
            JOIN documents d ON d.hash = cv.hash
            WHERE vv.embedding MATCH ? AND k = ?
            ORDER BY vv.distance
        """,
            (query_vec, limit),
        ).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"Direct vsearch failed: {e}")
        return []


def check_recall(db: sqlite3.Connection | None = None) -> dict:
    """
    Run recall check queries via direct vector search.
    Returns {score, passed, total, detail}.
    Score is fraction of queries where gold path appears in top-5.

    If db is None, opens the kairix DB internally (requires sqlite-vec).
    """
    from kairix.db import get_db_path, load_extensions

    close_db = False
    if db is None:
        db = sqlite3.connect(str(get_db_path()))
        load_extensions(db)
        close_db = True

    queries = _get_recall_queries()
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

        files = _vsearch_direct(db, query_vec)
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

    if close_db:
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
    logger.info(f"Recall check: {result['passed']}/{result['total']} ({score:.0%})")

    if prev_score is not None:
        delta = score - prev_score
        if delta < -DEGRADATION_THRESHOLD:
            msg = (
                f"⚠️ Recall degraded: {score:.0%} (was {prev_score:.0%}, Δ{delta:+.0%}). "
                f"Check azure-embed.log and run kairix onboard check."
            )
            logger.warning(msg)
            if alert_callback:
                alert_callback(msg)
            return False, result

    return True, result
