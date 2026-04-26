"""
kairix.temporal.index — Date-range query interface over temporal chunks.

Scans Kanban board files and daily memory logs, chunks them, then ranks
chunks against a topic string using lightweight BM25 token scoring.

Functions:
  get_memory_log_paths(start, end) → list[str]
  query_temporal_chunks(topic, start, end, chunk_types, limit) → list[TemporalChunk]

Never raises — returns [] on any failure.
"""

from __future__ import annotations

import logging
import math
import os as _os
import re
from collections import Counter
from datetime import date
from pathlib import Path

from kairix.temporal.chunker import TemporalChunk, chunk_board, chunk_memory_log

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DOCUMENT_ROOT = _os.environ.get("KAIRIX_DOCUMENT_ROOT") or _os.environ.get(
    "KAIRIX_VAULT_ROOT", str(Path.home() / "kairix-vault")
)
_WORKSPACE_ROOT = _os.environ.get("KAIRIX_WORKSPACE_ROOT", str(Path.home() / ".kairix" / "workspaces"))
# override with KAIRIX_BOARDS_DIR env var
_BOARDS_DIR = _os.environ.get("KAIRIX_BOARDS_DIR", f"{_DOCUMENT_ROOT}/01-Projects/Boards")

_BOARDS_GLOB = f"{_BOARDS_DIR}/*.md"
_WORKSPACE_MEMORY_GLOB = f"{_WORKSPACE_ROOT}/*/memory"

# Filename pattern for memory logs
_MEMORY_LOG_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


# ---------------------------------------------------------------------------
# Memory log path discovery
# ---------------------------------------------------------------------------


def get_memory_log_paths(
    start: date | None,
    end: date | None,
) -> list[str]:
    """
    Return all memory log paths across agent workspaces, filtered by date range.

    Scans /data/workspaces/*/memory/ for YYYY-MM-DD.md files.
    If start is None, returns all logs up to end.
    If end is None, returns all logs from start.
    If both are None, returns all logs found.

    Args:
        start: Inclusive start date (or None for no lower bound).
        end:   Inclusive end date (or None for no upper bound).

    Returns:
        Sorted list of matching file paths.
    """
    paths: list[str] = []

    for workspace_dir in Path(_WORKSPACE_ROOT).iterdir():
        memory_dir = workspace_dir / "memory"
        if not memory_dir.is_dir():
            continue

        for log_file in memory_dir.iterdir():
            m = _MEMORY_LOG_FILENAME_RE.match(log_file.name)
            if not m:
                continue
            try:
                log_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue

            if start is not None and log_date < start:
                continue
            if end is not None and log_date > end:
                continue

            paths.append(str(log_file))

    paths.sort()
    return paths


# ---------------------------------------------------------------------------
# Lightweight BM25 scorer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_STOP_WORDS = frozenset(
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
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "about",
        "and",
        "or",
        "but",
        "not",
        "so",
        "if",
        "as",
        "it",
        "its",
        "this",
        "that",
        "from",
        "what",
        "when",
        "where",
        "who",
        "which",
        "how",
        "me",
        "my",
        "we",
        "our",
        "you",
        "he",
        "she",
        "they",
        "them",
        "their",
        "i",
        "get",
        "use",
        "all",
        "any",
        "no",
        "up",
        "out",
        "then",
        "than",
    }
)

# BM25 tuning constants
_K1 = 1.5
_B = 0.75


def _tokenise(text: str) -> list[str]:
    """Tokenise text into lowercase non-stop-word tokens."""
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP_WORDS and len(t) >= 2]


def _bm25_score(query_tokens: list[str], doc_tokens: list[str], avg_dl: float) -> float:
    """
    Compute a simple BM25 score for a document against query tokens.

    Uses raw token frequencies without IDF (single-batch scoring — no corpus stats).
    This is a tf-normalised approximation suitable for small chunk sets.
    """
    if not query_tokens or not doc_tokens:
        return 0.0

    dl = len(doc_tokens)
    tf_counts = Counter(doc_tokens)
    score = 0.0

    for qt in query_tokens:
        tf = tf_counts.get(qt, 0)
        if tf == 0:
            continue
        # BM25 TF normalisation (IDF approximated as 1.0)
        numerator = tf * (_K1 + 1)
        denominator = tf + _K1 * (1 - _B + _B * (dl / max(avg_dl, 1)))
        score += numerator / denominator

    return score


def _recency_factor(chunk_date: date | None, end: date | None) -> float:
    """
    Compute a [0, 1] recency multiplier based on how old the chunk is.

    Chunks with date=None get a neutral 0.5 factor.
    The reference point is `end` (or today if end is None).
    """
    if chunk_date is None:
        return 0.5

    ref = end or date.today()
    age_days = max(0, (ref - chunk_date).days)

    # Exponential decay: half-life of 30 days
    return math.exp(-age_days / 30.0)


# ---------------------------------------------------------------------------
# Public query interface
# ---------------------------------------------------------------------------


def query_temporal_chunks(
    topic: str,
    start: date | None,
    end: date | None,
    chunk_types: list[str] | None = None,
    limit: int = 20,
) -> list[TemporalChunk]:
    """
    Query the temporal chunk store for chunks matching topic in the date range.

    Strategy:
      1. Scan all board files for Kanban cards
      2. Scan memory logs in the date range
      3. Filter by date range and optional chunk_types
      4. Score each chunk with BM25 x recency
      5. Return top-N by combined score

    Args:
        topic:       Topic string to rank chunks against.
        start:       Inclusive start date (None = no lower bound).
        end:         Inclusive end date (None = no upper bound).
        chunk_types: Optional filter — "board_card" and/or "memory_section".
                     If None, both types are included.
        limit:       Maximum number of chunks to return.

    Returns:
        List of TemporalChunk objects sorted by score (best first).
        Returns [] on any failure.
    """
    try:
        all_chunks: list[TemporalChunk] = []

        # 1. Board files
        for board_path in sorted(Path(_BOARDS_DIR).glob("*.md")):
            try:
                all_chunks.extend(chunk_board(str(board_path)))
            except Exception as e:
                logger.warning("query_temporal_chunks: error chunking board %r — %s", board_path, e)

        # 2. Memory logs in date range
        memory_paths = get_memory_log_paths(start, end)
        for log_path in memory_paths:
            try:
                all_chunks.extend(chunk_memory_log(log_path))
            except Exception as e:
                logger.warning("query_temporal_chunks: error chunking memory log %r — %s", log_path, e)

        # 3. Filter by date range
        date_filtered: list[TemporalChunk] = []
        for chunk in all_chunks:
            # Memory log chunks: already filtered by filename date above
            # Board card chunks: apply date filter if chunk has a date
            if chunk.chunk_type == "board_card" and chunk.date is not None:
                if start is not None and chunk.date < start:
                    continue
                if end is not None and chunk.date > end:
                    continue
            date_filtered.append(chunk)

        # 4. Filter by chunk_type
        if chunk_types is not None:
            date_filtered = [c for c in date_filtered if c.chunk_type in chunk_types]

        if not date_filtered:
            return []

        # 5. BM25 x recency scoring
        query_tokens = _tokenise(topic)
        all_doc_tokens = [_tokenise(c.text) for c in date_filtered]
        avg_dl = sum(len(t) for t in all_doc_tokens) / max(len(all_doc_tokens), 1)

        scored: list[tuple[float, TemporalChunk]] = []
        for chunk, doc_tokens in zip(date_filtered, all_doc_tokens, strict=True):
            bm25 = _bm25_score(query_tokens, doc_tokens, avg_dl)
            recency = _recency_factor(chunk.date, end)
            combined = bm25 * (0.7 + 0.3 * recency)  # weight: 70% relevance, 30% recency
            scored.append((combined, chunk))

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)

        return [chunk for _, chunk in scored[:limit]]

    except Exception as e:
        logger.warning("query_temporal_chunks: unexpected error — %s", e)
        return []
