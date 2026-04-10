"""
Vector search wrapper for the Mnemosyne hybrid search pipeline.

Uses the CTE pattern established in Phase 0 recall_check.py:_vsearch_direct.
Queries the sqlite-vec virtual table via a JOIN CTE to avoid direct vec0 bugs.

Returns [] on any failure (extension not loaded, DB locked, no results).
Never raises — caller treats [] as fallback signal.

VecResult fields:
  hash_seq   — "{hash}_{seq}" primary key in vectors_vec
  distance   — cosine distance (lower = more similar)
  path       — vault-relative file path
  collection — QMD collection name
  title      — document title
  snippet    — content excerpt
"""

import logging
import sqlite3
import struct
from typing import TypedDict

logger = logging.getLogger(__name__)

# Default number of results to return
VECTOR_DEFAULT_K: int = 10


class VecResult(TypedDict):
    """Single vector search result from sqlite-vec CTE query."""

    hash_seq: str
    distance: float
    path: str
    collection: str
    title: str
    snippet: str


def vector_search(
    db: sqlite3.Connection,
    query_vec: list[float],
    k: int = VECTOR_DEFAULT_K,
    collections: list[str] | None = None,
) -> list[VecResult]:
    """
    Run a vector similarity search via the CTE pattern against vectors_vec.

    Reuses the pattern from mnemosyne/embed/recall_check.py:_vsearch_direct.
    The CTE avoids direct JOIN on vec0 virtual table which causes errors.

    Args:
        db:          Open sqlite3.Connection with sqlite-vec extension loaded.
        query_vec:   Query embedding as list of floats (1536 dims).
        k:           Number of results to return.
        collections: Optional collection filter — if provided, only return
                     results from these collections.

    Returns:
        List of VecResult dicts sorted by distance (ascending, most similar first).
        Returns [] if extension is not loaded, DB is locked, query_vec is empty,
        or any other failure occurs.
        Never raises.
    """
    if not query_vec:
        return []

    try:
        query_bytes = struct.pack(f"<{len(query_vec)}f", *query_vec)
    except (struct.error, TypeError) as e:
        logger.warning("vector_search: failed to pack query vector — %s", e)
        return []

    return _vsearch_with_bytes(db, query_bytes, k, collections)


def vector_search_bytes(
    db: sqlite3.Connection,
    query_bytes: bytes,
    k: int = VECTOR_DEFAULT_K,
    collections: list[str] | None = None,
    date_filter_paths: frozenset[str] | None = None,
) -> list[VecResult]:
    """
    Vector search using pre-packed float32 bytes (as returned by embed_text_as_bytes).

    Returns [] on any failure. Never raises.
    """
    if not query_bytes:
        return []
    results = _vsearch_with_bytes(db, query_bytes, k, collections)
    # TMP-2: apply date-range path filter for TEMPORAL queries
    if date_filter_paths:
        results = [r for r in results if r["path"] in date_filter_paths]
    return results


def _vsearch_with_bytes(
    db: sqlite3.Connection,
    query_bytes: bytes,
    k: int,
    collections: list[str] | None,
) -> list[VecResult]:
    """
    Internal implementation — CTE-based vector search.

    Pattern sourced from recall_check.py:_vsearch_direct, extended with
    collection filtering and additional result fields.
    """
    try:
        # sqlite-vec v0.1.7-alpha.2 constraint: MATCH must be the primary table
        # in its own SELECT; JOINs in the outer CTE cause "near MATCH: syntax error".
        # Collection filter applied in outer query after the CTE.
        if collections:
            placeholders = ",".join("?" * len(collections))
            sql = (
                "WITH knn AS ("
                "  SELECT hash_seq, distance FROM vectors_vec"
                "  WHERE embedding MATCH ? AND k = ?"
                "  ORDER BY distance"
                ") "
                "SELECT knn.hash_seq, knn.distance, d.path, d.collection, d.title, "
                "COALESCE(c.doc, '') AS snippet "
                "FROM knn "
                "JOIN content_vectors cv ON knn.hash_seq = cv.hash || '_' || cv.seq "
                "JOIN documents d ON d.hash = cv.hash "
                "LEFT JOIN content c ON c.hash = cv.hash "
                f"WHERE d.collection IN ({placeholders}) "
                "ORDER BY knn.distance"
            )
            params: list = [query_bytes, k, *collections]
        else:
            sql = """
                WITH knn AS (
                    SELECT hash_seq, distance FROM vectors_vec
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                )
                SELECT
                    knn.hash_seq,
                    knn.distance,
                    d.path,
                    d.collection,
                    d.title,
                    COALESCE(c.doc, '') AS snippet
                FROM knn
                JOIN content_vectors cv ON knn.hash_seq = cv.hash || '_' || cv.seq
                JOIN documents d ON d.hash = cv.hash
                LEFT JOIN content c ON c.hash = cv.hash
                ORDER BY knn.distance
            """
            params = [query_bytes, k]

        rows = db.execute(sql, params).fetchall()

        results: list[VecResult] = []
        for row in rows:
            results.append(
                VecResult(
                    hash_seq=str(row[0]),
                    distance=float(row[1]),
                    path=str(row[2]),
                    collection=str(row[3]),
                    title=str(row[4] or ""),
                    snippet=str(row[5] or ""),
                )
            )
        return results

    except sqlite3.OperationalError as e:
        # Extension not loaded, table doesn't exist, DB locked, etc.
        logger.warning("vector_search: sqlite3.OperationalError — %s", e)
        return []
    except sqlite3.DatabaseError as e:
        logger.warning("vector_search: sqlite3.DatabaseError — %s", e)
        return []
    except Exception as e:
        logger.warning("vector_search: unexpected error — %s", e)
        return []
