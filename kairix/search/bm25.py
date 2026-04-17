"""
BM25 search wrapper for the kairix hybrid search pipeline.

Runs `qmd search --json` as a subprocess and returns structured results.
Never raises — returns [] on any failure (timeout, parse error, empty).

Result format mirrors what qmd outputs:
  {file, title, snippet, score, collection}

BM25Result is a TypedDict for lightweight, serialisable results.

Timeout: 5 seconds (configurable via BM25_TIMEOUT_S module constant).

Note: `qmd search --json --collection <name>` has a known QMD CLI bug where
multi-word queries return empty results when --collection is specified alongside --json.
The vault-entities collection is searched via direct SQLite FTS as a workaround.
Single-collection calls (non-vault-entities) and no-collection calls work correctly.
"""

import json
import logging
import math
import sqlite3
import subprocess
from pathlib import Path
from typing import TypedDict

from kairix._qmd import get_qmd_binary

# Path to QMD's SQLite index for direct FTS queries (vault-entities workaround)
_QMD_DB_PATH: str = str(Path.home() / ".cache/qmd/index.sqlite")

logger = logging.getLogger(__name__)

# Timeout for qmd subprocess call (seconds)
BM25_TIMEOUT_S: float = 5.0

# Default result limit
BM25_DEFAULT_LIMIT: int = 10


class BM25Result(TypedDict):
    """Single BM25 search result from qmd search --json."""

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
    Normalise a natural-language query for FTS5.

    - Remove stop words
    - Replace hyphens with spaces (FTS5 treats '-' as NOT operator)
    - Quote tokens that contain special FTS characters
    - Return remaining tokens joined with implicit AND (space)
    - Returns empty string if no meaningful tokens remain
    """
    import re

    # Replace hyphens with spaces first
    query = query.replace("-", " ")
    # Extract word tokens (alphanumeric sequences)
    raw_tokens = re.findall(r"[a-zA-Z0-9']+", query.lower())
    # Filter stop words and very short tokens
    tokens = [t for t in raw_tokens if t not in _FTS_STOP_WORDS and len(t) >= 2]
    return " ".join(tokens)


def _bm25_direct_db(
    query: str,
    collection: str,
    limit: int,
) -> list[BM25Result]:
    """
    Query a single collection via direct SQLite FTS.

    Workaround for the QMD CLI bug where `--json --collection` returns empty
    results for multi-word queries.  Used for vault-entities only.

    Returns [] on any error.  Never raises.
    """
    fts_query = _normalise_fts_query(query)
    if not fts_query:
        logger.debug("_bm25_direct_db: empty FTS query after normalisation (original=%r)", query[:60])
        return []

    try:
        db = sqlite3.connect(_QMD_DB_PATH, timeout=5.0)
        db.row_factory = sqlite3.Row

        rows = db.execute(
            """
            SELECT d.collection,
                   d.path,
                   d.title,
                   c.doc,
                   documents_fts.rank AS rank
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            JOIN content   c ON c.hash = d.hash
            WHERE documents_fts MATCH ?
              AND d.collection = ?
            ORDER BY documents_fts.rank
            LIMIT ?
            """,
            (fts_query, collection, limit),
        ).fetchall()
        db.close()
    except Exception as e:
        logger.debug("_bm25_direct_db: error querying %s — %s", collection, e)
        return []

    results: list[BM25Result] = []
    for row in rows:
        # FTS5 rank is negative (more negative = better).  Normalise to [0, 1].
        raw_rank = float(row["rank"])
        # Map using sigmoid-like compression so scores cluster near 0.5-0.9
        score = 1.0 / (1.0 + math.exp(raw_rank * 0.3))

        # Build a short snippet from doc (first 300 chars of body)
        doc_text = row["doc"] or ""
        # Skip YAML frontmatter
        if doc_text.startswith("---"):
            parts = doc_text.split("---", 2)
            snippet = parts[2].strip()[:300] if len(parts) >= 3 else doc_text[:300]
        else:
            snippet = doc_text[:300]

        file_uri = f"qmd://{collection}/{row['path']}"
        results.append(
            BM25Result(
                file=file_uri,
                title=str(row["title"] or ""),
                snippet=snippet,
                score=score,
                collection=collection,
            )
        )

    return results


def _path_from_file_uri(file_uri: str) -> str:
    """Extract vault-relative path from a QMD file URI.

    QMD file URIs have the format ``qmd://{collection}/{path}``.
    Returns the vault-relative ``{path}`` component.
    Falls back to returning the full URI unchanged for non-qmd URIs.
    """
    if "://" in file_uri:
        after_scheme = file_uri.split("://", 1)[1]
        parts = after_scheme.split("/", 1)
        return parts[1] if len(parts) == 2 else after_scheme
    return file_uri


def bm25_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = BM25_DEFAULT_LIMIT,
    agent: str | None = None,
    date_filter_paths: frozenset[str] | None = None,
) -> list[BM25Result]:
    """
    Run BM25 search via qmd subprocess.

    Args:
        query:        Search query string.
        collections:  Optional list of collection names to restrict search.
        limit:        Maximum number of results to return.
        agent:        Optional agent name — reserved for future collection scoping.

    Returns:
        List of BM25Result dicts. Returns [] on any failure.
        Never raises.
    """
    if not query or not query.strip():
        return []

    # vault-entities uses direct DB FTS to work around QMD CLI bug:
    # `qmd search --json --collection vault-entities` returns empty for multi-word queries.
    # Other collections use the normal CLI path.
    direct_db_collections = {"vault-entities"}

    cli_collections: list[str] = []
    direct_results: list[BM25Result] = []

    if collections:
        for col in collections:
            if col in direct_db_collections:
                direct_results.extend(_bm25_direct_db(query, col, limit))
            else:
                cli_collections.append(col)
    else:
        cli_collections = []  # no collection filter → qmd searches all

    # If all collections were handled via direct DB, return early
    if collections and not cli_collections:
        return direct_results

    try:
        binary = get_qmd_binary()
    except FileNotFoundError as e:
        logger.warning("BM25 search: qmd binary not found — %s", e)
        return direct_results

    # Sanitize query for QMD/FTS5: hyphens are treated as NOT operators by FTS5,
    # causing zero results for hyphenated terms (e.g. "vm-tc-openclaw", "FEAT-020").
    # Replace with spaces to restore AND semantics. Underscores are handled by
    # FTS5's unicode61 tokenizer (splits on connector punctuation) — no change needed.
    qmd_query = query.replace("-", " ")
    cmd: list[str] = [binary, "search", "--json", "--limit", str(limit), qmd_query]

    if cli_collections:
        for col in cli_collections:
            cmd.extend(["--collection", col])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=BM25_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.warning("BM25 search: qmd subprocess timed out after %ss (query=%r)", BM25_TIMEOUT_S, query[:60])
        return []
    except OSError as e:
        logger.warning("BM25 search: failed to launch qmd — %s", e)
        return []
    except Exception as e:
        logger.warning("BM25 search: unexpected error launching qmd — %s", e)
        return []

    if result.returncode != 0:
        logger.warning(
            "BM25 search: qmd exited %d (query=%r, stderr=%r)",
            result.returncode,
            query[:60],
            result.stderr[:200],
        )
        return direct_results

    cli_results = _parse_bm25_output(result.stdout, query)
    # Merge: direct DB results come first (already scored), then CLI results
    # Dedup by file URI
    seen: set[str] = {r["file"] for r in direct_results}
    merged = list(direct_results)
    for r in cli_results:
        if r["file"] not in seen:
            merged.append(r)
            seen.add(r["file"])
    # TMP-2: apply date-range path filter for TEMPORAL queries
    if date_filter_paths:
        merged = [r for r in merged if _path_from_file_uri(r["file"]) in date_filter_paths]

    return merged


def _parse_bm25_output(stdout: str, query: str = "") -> list[BM25Result]:
    """
    Parse qmd --json output into a list of BM25Result.

    Returns [] on any parse failure.
    """
    if not stdout or not stdout.strip():
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.warning("BM25 search: failed to parse JSON output — %s (query=%r)", e, query[:60])
        return []

    if not isinstance(data, list):
        # qmd might return {"results": [...]} in some versions
        if isinstance(data, dict) and "results" in data:
            data = data["results"]
        else:
            logger.warning("BM25 search: unexpected JSON structure from qmd (query=%r)", query[:60])
            return []

    results: list[BM25Result] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        results.append(
            BM25Result(
                file=str(item.get("file", item.get("path", ""))),
                title=str(item.get("title", "")),
                snippet=str(item.get("snippet", item.get("content", ""))),
                score=float(item.get("score", 0.0)),
                collection=str(item.get("collection", "")),
            )
        )

    return results
