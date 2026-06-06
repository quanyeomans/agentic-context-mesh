"""
kairix.core.temporal.index — Date-range query interface over temporal chunks.

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
import re
from collections import Counter
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from kairix.core.search.bm25 import FTS_STOP_WORDS as _STOP_WORDS
from kairix.core.temporal.chunker import TemporalChunk, chunk_board, chunk_memory_log
from kairix.paths import boards_dir_override as _boards_dir_override
from kairix.paths import document_root as _doc_root_fn

logger = logging.getLogger(__name__)

# Filename pattern for memory logs
_MEMORY_LOG_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


# ---------------------------------------------------------------------------
# Memory log path discovery
# ---------------------------------------------------------------------------


def _boards_dir(document_root: Path | None = None) -> Path:
    """Return the boards directory, respecting KAIRIX_BOARDS_DIR override.

    ``document_root`` is an injectable seam (defaults to the production
    ``paths.document_root()``) so tests can pass a tmp-path-rooted directory
    without monkeypatching env vars or the paths module.
    """
    override = _boards_dir_override()
    if override is not None:
        return override
    root = document_root if document_root is not None else _doc_root_fn()
    return root / "01-Projects" / "Boards"


def _memory_log_date(filename: str) -> date | None:
    """Parse a memory-log filename (``YYYY-MM-DD.md``) into a date, or None."""
    m = _MEMORY_LOG_FILENAME_RE.match(filename)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _date_in_range(log_date: date, start: date | None, end: date | None) -> bool:
    """Inclusive range check; ``None`` bounds are treated as open."""
    if start is not None and log_date < start:
        return False
    if end is not None and log_date > end:
        return False
    return True


def _iter_scope_memory_dirs(config: dict[str, object] | None) -> Iterator[Path]:
    """Yield every memory surface declared in the parsed ``agents:`` config.

    Replaces the pre-PR-1.2 ``<agent>/memory`` filesystem scan: the
    surfaces now come from operator config (``agents.<name>.surfaces``)
    via :func:`kairix.core.agents.scope.load_agent_scopes`, so flat
    vault layouts and multi-surface scopes (memory + workspace) are
    both honoured. Surfaces that don't exist on disk are skipped so
    a partially-onboarded scope doesn't break the temporal index.
    """
    from kairix.core.agents.scope import load_agent_scopes

    scopes = load_agent_scopes(config)
    for scope in scopes.values():
        for path in scope.memory_paths():
            if path.is_dir():
                yield path


def _load_top_level_config() -> dict[str, object] | None:
    """Read ``kairix.config.yaml`` as a top-level dict, or None on missing.

    Thin alias over :func:`kairix.paths.load_top_level_config`.
    """
    from kairix.paths import load_top_level_config

    return load_top_level_config()


def get_memory_log_paths(
    start: date | None,
    end: date | None,
    document_root: Path | None = None,
    config: dict[str, object] | None = None,
) -> list[str]:
    """
    Return all memory log paths across every configured agent surface,
    filtered by date range.

    Each surface returned by
    :meth:`kairix.core.agents.scope.AgentScope.memory_paths` is scanned
    for ``YYYY-MM-DD.md`` files. If start is None, returns all logs up
    to end. If end is None, returns all logs from start. If both are
    None, returns all logs found.

    Args:
        start:         Inclusive start date (or None for no lower bound).
        end:           Inclusive end date (or None for no upper bound).
        document_root: Reserved seam — currently unused, retained for
                       caller compatibility. PR 1.2 routes via AgentScope
                       which carries absolute paths.
        config:        Test seam — pass a parsed ``kairix.config.yaml``
                       dict (with an ``agents:`` block) to drive
                       discovery without reading the on-disk config.
                       Production callers leave None.

    Returns:
        Sorted list of matching file paths.
    """
    _ = document_root  # reserved seam — surfaces are absolute in AgentScope
    cfg = config if config is not None else _load_top_level_config()
    paths: list[str] = []
    for memory_dir in _iter_scope_memory_dirs(cfg):
        for log_file in memory_dir.iterdir():
            log_date = _memory_log_date(log_file.name)
            if log_date is None or not _date_in_range(log_date, start, end):
                continue
            paths.append(str(log_file))

    paths.sort()
    return paths


# ---------------------------------------------------------------------------
# Lightweight BM25 scorer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")

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


def _collect_board_chunks(document_root: Path | None) -> list[TemporalChunk]:
    """Scan Kanban board markdown files and emit their chunks; per-file errors logged."""
    chunks: list[TemporalChunk] = []
    boards = _boards_dir(document_root=document_root)
    if not boards.is_dir():
        return chunks
    for board_path in sorted(boards.glob("*.md")):
        try:
            chunks.extend(chunk_board(str(board_path)))
        except Exception as e:
            logger.warning("query_temporal_chunks: error chunking board %r — %s", board_path, e)
    return chunks


def _collect_memory_chunks(
    start: date | None,
    end: date | None,
    document_root: Path | None,
    config: dict[str, object] | None = None,
) -> list[TemporalChunk]:
    """Scan in-range memory logs and emit their chunks; per-file errors logged."""
    chunks: list[TemporalChunk] = []
    for log_path in get_memory_log_paths(start, end, document_root=document_root, config=config):
        try:
            chunks.extend(chunk_memory_log(log_path))
        except Exception as e:
            logger.warning("query_temporal_chunks: error chunking memory log %r — %s", log_path, e)
    return chunks


def _filter_chunks(
    chunks: list[TemporalChunk],
    start: date | None,
    end: date | None,
    chunk_types: list[str] | None,
) -> list[TemporalChunk]:
    """Apply date-range and chunk-type filters; memory chunks pre-filtered upstream."""
    out: list[TemporalChunk] = []
    for chunk in chunks:
        # Board card chunks: enforce date filter when the chunk carries a date.
        # Memory log chunks: already filtered by filename date in get_memory_log_paths.
        if chunk.chunk_type == "board_card" and chunk.date is not None:
            if not _date_in_range(chunk.date, start, end):
                continue
        out.append(chunk)
    if chunk_types is not None:
        out = [c for c in out if c.chunk_type in chunk_types]
    return out


def _rank_chunks(topic: str, chunks: list[TemporalChunk], end: date | None, limit: int) -> list[TemporalChunk]:
    """Score each chunk with BM25 x recency, return top-N."""
    query_tokens = _tokenise(topic)
    all_doc_tokens = [_tokenise(c.text) for c in chunks]
    avg_dl = sum(len(t) for t in all_doc_tokens) / max(len(all_doc_tokens), 1)

    scored: list[tuple[float, TemporalChunk]] = []
    for chunk, doc_tokens in zip(chunks, all_doc_tokens, strict=True):
        bm25 = _bm25_score(query_tokens, doc_tokens, avg_dl)
        recency = _recency_factor(chunk.date, end)
        combined = bm25 * (0.7 + 0.3 * recency)  # weight: 70% relevance, 30% recency
        scored.append((combined, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


def query_temporal_chunks(
    topic: str,
    start: date | None,
    end: date | None,
    chunk_types: list[str] | None = None,
    limit: int = 20,
    document_root: Path | None = None,
    config: dict[str, object] | None = None,
) -> list[TemporalChunk]:
    """
    Query the temporal chunk store for chunks matching topic in the date range.

    Strategy:
      1. Scan all board files for Kanban cards (driven by ``document_root``)
      2. Scan memory logs across every configured agent surface (driven by
         the ``agents:`` block in the parsed ``config`` dict — falling back
         to ``kairix.config.yaml`` on disk when ``config`` is None)
      3. Filter by date range and optional chunk_types
      4. Score each chunk with BM25 x recency
      5. Return top-N by combined score

    Args:
        topic:         Topic string to rank chunks against.
        start:         Inclusive start date (None = no lower bound).
        end:           Inclusive end date (None = no upper bound).
        chunk_types:   Optional filter — "board_card" and/or "memory_section".
                       If None, both types are included.
        limit:         Maximum number of chunks to return.
        document_root: Override for the boards document root directory.
                       Defaults to paths.document_root() when None.
        config:        Optional parsed ``kairix.config.yaml`` dict — test
                       seam for driving memory-surface discovery without
                       reading the on-disk config.

    Returns:
        List of TemporalChunk objects sorted by score (best first).
        Returns [] on any failure.
    """
    try:
        all_chunks = _collect_board_chunks(document_root) + _collect_memory_chunks(
            start, end, document_root, config=config
        )
        filtered = _filter_chunks(all_chunks, start, end, chunk_types)
        if not filtered:
            return []
        return _rank_chunks(topic, filtered, end, limit)
    except Exception as e:
        logger.warning("query_temporal_chunks: unexpected error — %s", e)
        return []
