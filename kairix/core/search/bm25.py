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
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

from kairix.core.db import get_db_path, open_db

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
    # MM-3 — per-page citation. ``None`` for non-paged documents
    # (passthrough markdown, vault notes); the connector pipeline populates
    # this from ``documents.source_page`` for PDF / PPTX / XLSX rows so
    # downstream renderers can quote a specific page back to the operator.
    source_page: int | None


FTS_STOP_WORDS: frozenset[str] = frozenset(
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

    Delegates to :func:`kairix.core.search.tokenizer.tokenize_fts_query`
    with ``style="prefix"`` and converts ``None`` to empty string for
    backwards compatibility.
    """
    from kairix.core.search.tokenizer import tokenize_fts_query

    return tokenize_fts_query(query, style="prefix") or ""


def _build_bm25_query(
    fts_query: str,
    collections: list[str] | None,
    limit: int,
) -> tuple[str, list]:
    """Build the FTS5 SQL query and parameter list.

    ``collections=None`` means "no scope filter — search all active
    documents". ``collections=[non-empty]`` filters via ``IN (...)``.
    ``collections=[]`` (explicit empty) is the caller's "search nothing"
    signal — the public ``bm25_search`` short-circuits before reaching this
    helper, so this function is only ever called with ``None`` or a
    non-empty list.
    """
    # The snippet is built by FTS5 ``snippet()`` on the ``doc`` column
    # (index 2 of ``documents_fts(filepath, title, doc)``) so it is a window
    # CENTRED on the matched terms, with a ``…`` ellipsis marking the
    # truncated edges — not a fixed prefix of the chunk opening (PLA-269).
    # ``snippet()`` reads from the FTS index's own stored copy of ``doc``, so
    # the ``content`` table no longer needs joining for the result snippet.
    if collections is not None:
        placeholders = ",".join("?" * len(collections))
        sql = f"""
            SELECT d.collection,
                   d.path,
                   d.title,
                   d.source_page,
                   snippet(documents_fts, 2, '', '', ' … ', 32) AS snippet,
                   bm25(documents_fts, 1.0, 1.0, 0.5) AS bm25_score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
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
                   d.source_page,
                   snippet(documents_fts, 2, '', '', ' … ', 32) AS snippet,
                   bm25(documents_fts, 1.0, 1.0, 0.5) AS bm25_score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
              AND d.active = 1
            ORDER BY bm25_score ASC
            LIMIT ?
        """
        params = [fts_query, limit]

    return sql, params


def _coerce_finite_score(score: Any, *, path: str) -> float:
    """Cast a score to ``float`` and clamp non-finite values to 0.

    Defence-in-depth for the doc_repo seam — even if a repository emits
    NaN / inf (pathological backend, bug, malicious test fake), the public
    surface must never propagate a non-finite score into RRF fusion.
    """
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        logger.warning(
            "bm25: non-finite score %r for path=%r via doc_repo; clamping to 0",
            score,
            path,
        )
        return 0.0
    return value


def _bm25_via_doc_repo(
    doc_repo: object,
    query: str,
    collections: list[str] | None,
    limit: int,
    date_filter_paths: frozenset[str] | None,
) -> list[BM25Result]:
    """Delegate BM25 to a DocumentRepository; map raw dicts to BM25Result."""
    try:
        raw = doc_repo.search_fts(query, collections=collections, limit=limit)  # type: ignore[union-attr] — duck-typed seam; mypy can't see through `object`
        results = []
        for r in raw:
            file_path = r.get("file", r.get("path", ""))
            raw_page = r.get("source_page")
            page_value: int | None = int(raw_page) if isinstance(raw_page, int) else None
            results.append(
                BM25Result(
                    file=file_path,
                    title=r.get("title", ""),
                    snippet=r.get("snippet", r.get("content", "")[:300]),
                    score=_coerce_finite_score(r.get("score", 0.0), path=file_path),
                    collection=r.get("collection", ""),
                    source_page=page_value,
                )
            )
        if date_filter_paths:
            results = [r for r in results if r["file"] in date_filter_paths]
        return results
    except Exception as e:
        logger.warning("bm25_search: doc_repo.search_fts failed — %s", e)
        return []


def _normalise_bm25_score(raw_score: float, *, path: str = "") -> float:
    """Normalise a raw BM25 score into the closed interval [0, 1].

    Maps ``|s| / (1 + |s|)``. Non-finite inputs (``nan`` / ``+inf`` /
    ``-inf``) are clamped to 0 with a logged warning — empty documents or
    a pathological FTS state can otherwise propagate ``nan`` through RRF
    fusion and silently rank-poison the pipeline.
    """
    if not math.isfinite(raw_score):
        logger.warning(
            "bm25: non-finite raw score %r for path=%r; clamping to 0",
            raw_score,
            path,
        )
        return 0.0
    return abs(raw_score) / (1.0 + abs(raw_score))


def _row_to_bm25_result(row: Any) -> BM25Result:
    """Map a single SQLite row from the FTS query into a BM25Result."""
    raw_score = float(row["bm25_score"])
    path = str(row["path"])
    raw_page: Any = None
    # Defensive — older test DBs may pre-date the source_page column.
    try:
        raw_page = row["source_page"]
    except (KeyError, IndexError):
        raw_page = None
    return BM25Result(
        file=path,
        # FTS5 ``snippet()`` window centred on the match (PLA-269), not a
        # fixed prefix of the chunk opening. ``or ""`` guards the rare
        # contentless-FTS row where ``snippet()`` yields NULL.
        snippet=str(row["snippet"] or ""),
        title=str(row["title"] or ""),
        score=_normalise_bm25_score(raw_score, path=path),
        collection=str(row["collection"]),
        source_page=int(raw_page) if isinstance(raw_page, int) else None,
    )


def bm25_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = BM25_DEFAULT_LIMIT,
    agent: str | None = None,
    date_filter_paths: frozenset[str] | None = None,
    db_path: Path | None = None,
    doc_repo: object | None = None,
) -> list[BM25Result]:
    """
    Run BM25 search via direct SQLite FTS5 query.

    Args:
        query:             Search query string.
        collections:       Optional list of collection names to restrict search.
        limit:             Maximum number of results to return.
        agent:             Optional agent name — reserved for future collection scoping.
        date_filter_paths: Optional set of paths to restrict results to (TEMPORAL).
        db_path:           Optional path to the SQLite database. Defaults to
                           get_db_path().
        doc_repo:          Optional DocumentRepository. When provided, delegates
                           to doc_repo.search_fts() instead of direct SQL.

    Returns:
        List of BM25Result dicts. Returns [] on any failure.
        Never raises.
    """
    # F19 — agent kwarg is reserved for future per-agent collection scoping
    # (see docstring). Acknowledge it here so the unused-params rule passes
    # without renaming a public-API kwarg that downstream callers already
    # bind by name.
    _ = agent

    if not query or not query.strip():
        return []

    # Explicit empty collections list means "search nothing — the caller
    # has narrowed the scope to zero collections". Distinct from
    # ``collections=None`` which means "no filter — search all active
    # documents". Without this guard, downstream code conflates the two
    # and silently returns global results when the caller meant zero.
    if collections is not None and len(collections) == 0:
        return []

    if doc_repo is not None:
        return _bm25_via_doc_repo(doc_repo, query, collections, limit, date_filter_paths)

    fts_query = _normalise_fts_query(query)
    if not fts_query:
        logger.debug("bm25_search: empty FTS query after normalisation (original=%r)", query[:60])
        return []

    try:
        resolved_path = db_path if db_path is not None else get_db_path()
        db = open_db(Path(resolved_path))
        db.row_factory = sqlite3.Row
    except Exception as e:
        logger.warning("bm25_search: cannot open database — %s", e)
        return []

    sql, params = _build_bm25_query(fts_query, collections, limit)

    try:
        rows = db.execute(sql, params).fetchall()
    except Exception as e:
        logger.warning("bm25_search: FTS query failed — %s (query=%r)", e, query[:60])
        db.close()
        return []

    results = [_row_to_bm25_result(row) for row in rows]
    db.close()
    if date_filter_paths:
        results = [r for r in results if r["file"] in date_filter_paths]
    return results
