"""
BM25 search for the kairix hybrid search pipeline.

Queries the kairix SQLite FTS5 index directly — no external subprocess.
Never raises — returns [] on any failure (DB locked, parse error, empty).

Result format:
  {file, title, snippet, score, collection}

BM25Result is a TypedDict for lightweight, serialisable results.
"""

import logging
import math
import re
import sqlite3
from typing import TypedDict

from kairix.db import get_db_path

logger = logging.getLogger(__name__)

# Default result limit — 20 provides more candidates for RRF fusion
BM25_DEFAULT_LIMIT: int = 20


class BM25Result(TypedDict):
    """Single BM25 search result."""

    file: str
    title: str
    snippet: str
    score: float
    collection: str


_FTS_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "from",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "not",
        "only",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "ours",
        "ourselves",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "he",
        "him",
        "his",
        "himself",
        "she",
        "her",
        "hers",
        "herself",
        "it",
        "its",
        "itself",
        "they",
        "them",
        "their",
        "theirs",
        "themselves",
        "i",
        "know",
        "tell",
        "us",
        "let",
        "get",
        "go",
        "make",
        "use",
        "and",
        "but",
        "or",
        "nor",
        "yet",
        "as",
        "if",
        "since",
        "while",
        "because",
        "although",
        "though",
        "unless",
        "until",
    }
)


def _normalise_fts_query(query: str) -> str:
    """
    Build an FTS5 query from natural language using quoted prefix match.

    Matches QMD's buildFTS5Query() behaviour: each meaningful token becomes
    ``"token"*`` (quoted with prefix wildcard), joined with AND. This gives
    exact token matches with prefix expansion, which is more precise than
    bare tokens for technical identifiers (ADR-012 → ``"adr"* AND "012"*``).

    Returns empty string if no meaningful tokens remain.
    """
    # Replace hyphens, underscores, and apostrophes with spaces
    query = query.replace("-", " ").replace("_", " ").replace("'", " ").replace("\u2019", " ")
    # Extract word tokens (alphanumeric sequences only)
    raw_tokens = re.findall(r"[a-zA-Z0-9]+", query.lower())
    # Filter stop words and very short tokens
    tokens = [t for t in raw_tokens if t not in _FTS_STOP_WORDS and len(t) >= 2]
    if not tokens:
        return ""
    # Quoted prefix match per token, AND-joined (matches QMD's search behaviour)
    return " AND ".join(f'"{t}"*' for t in tokens)


def bm25_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = BM25_DEFAULT_LIMIT,
    agent: str | None = None,
    date_filter_paths: frozenset[str] | None = None,
) -> list[BM25Result]:
    """
    Run BM25 search via direct SQLite FTS5 query.

    Args:
        query:             Search query string.
        collections:       Optional list of collection names to restrict search.
        limit:             Maximum number of results to return.
        agent:             Optional agent name — reserved for future collection scoping.
        date_filter_paths: Optional set of paths to restrict results to (TEMPORAL).

    Returns:
        List of BM25Result dicts. Returns [] on any failure.
        Never raises.
    """
    if not query or not query.strip():
        return []

    fts_query = _normalise_fts_query(query)
    if not fts_query:
        logger.debug("bm25_search: empty FTS query after normalisation (original=%r)", query[:60])
        return []

    try:
        db_path = get_db_path()
        db = sqlite3.connect(str(db_path), timeout=5.0)
        db.row_factory = sqlite3.Row
    except Exception as e:
        logger.warning("bm25_search: cannot open database — %s", e)
        return []

    try:
        # Use bm25() auxiliary function with column weights (filepath, title, doc).
        # Default weights from sweep: equal weights work well; prefix query style
        # has more impact than weight tuning.
        if collections:
            placeholders = ",".join("?" * len(collections))
            sql = f"""
                SELECT d.collection,
                       d.path,
                       d.title,
                       c.doc,
                       bm25(documents_fts, 1.0, 1.0, 1.0) AS bm25_score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                JOIN content   c ON c.hash = d.hash
                WHERE documents_fts MATCH ?
                  AND d.collection IN ({placeholders})
                  AND d.active = 1
                ORDER BY bm25_score ASC
                LIMIT ?
            """
            params: list = [fts_query, *collections, limit]
        else:
            sql = """
                SELECT d.collection,
                       d.path,
                       d.title,
                       c.doc,
                       bm25(documents_fts, 1.0, 1.0, 1.0) AS bm25_score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                JOIN content   c ON c.hash = d.hash
                WHERE documents_fts MATCH ?
                  AND d.active = 1
                ORDER BY bm25_score ASC
                LIMIT ?
            """
            params = [fts_query, limit]

        rows = db.execute(sql, params).fetchall()
    except Exception as e:
        logger.warning("bm25_search: FTS query failed — %s (query=%r)", e, query[:60])
        db.close()
        return []

    results: list[BM25Result] = []
    for row in rows:
        # bm25() returns negative values (lower = better match).
        # Normalise to [0, 1) using QMD's formula: |x| / (1 + |x|)
        # Maps: strong(-10)→0.91, medium(-2)→0.67, weak(-0.5)→0.33, none(0)→0
        raw_score = float(row["bm25_score"])
        score = abs(raw_score) / (1.0 + abs(raw_score))

        # Build a short snippet from doc (first 300 chars of body)
        doc_text = row["doc"] or ""
        if doc_text.startswith("---"):
            parts = doc_text.split("---", 2)
            snippet = parts[2].strip()[:300] if len(parts) >= 3 else doc_text[:300]
        else:
            snippet = doc_text[:300]

        results.append(
            BM25Result(
                file=str(row["path"]),
                title=str(row["title"] or ""),
                snippet=snippet,
                score=score,
                collection=str(row["collection"]),
            )
        )

    db.close()

    # TMP-2: apply date-range path filter for TEMPORAL queries
    if date_filter_paths:
        results = [r for r in results if r["file"] in date_filter_paths]

    return results
