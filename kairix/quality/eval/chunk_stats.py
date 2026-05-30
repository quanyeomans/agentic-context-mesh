"""Per-source-type chunk-size distribution telemetry.

ADR-028 §"Quality evaluation" #4 — surfaces the third measurement
scaffolding (alongside per-type Recall@k slicing and the canary suite).
Reads ``content_vectors`` joined against ``documents.collection`` and
``content.doc`` to compute the mean / p50 / p95 / p99 chunk size per
source type.

The CLI subcommand ``kairix eval chunk-stats`` wraps this module —
operators run it after an ingest to spot fragmentation (long tail of
tiny chunks) or over-uniform splitting (near-flat 512-char chunks
everywhere).

Source-type derivation: each chunk's owning document has a
``documents.path`` whose extension maps via
:func:`_extension_to_source_type` to a canonical slug
(``markdown``/``pptx``/``pdf``/``docx``/``xlsx``/``email``/``calendar``).
Per-type-fixture collections that follow the
``per-type-fixtures/<type>`` layout are recognised directly from the
collection name; this lets the chunk-stats CLI report on a corpus that
hasn't been indexed by extension yet.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class ChunkStats:
    """Per-source-type chunk-size summary.

    All size measurements are in characters. ``n`` is the chunk count
    for the slice; mean / p50 / p95 / p99 are computed from the sorted
    size distribution. ``mean`` is rounded to one decimal place to keep
    the rendered table compact.
    """

    source_type: str
    n: int
    mean: float
    p50: int
    p95: int
    p99: int


_EXTENSION_TO_SOURCE_TYPE: dict[str, str] = {
    "md": "markdown",
    "markdown": "markdown",
    "pptx": "pptx",
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
    "xls": "xlsx",
    "eml": "email",
    "msg": "email",
    "ics": "calendar",
    "ical": "calendar",
}

_PER_TYPE_FIXTURE_COLLECTION_PREFIX = "per-type-fixtures/"


def _extension_to_source_type(path: str) -> str:
    """Map a document path to a canonical source-type slug."""
    if not path:
        return "unknown"
    if path.startswith(_PER_TYPE_FIXTURE_COLLECTION_PREFIX):
        tail = path[len(_PER_TYPE_FIXTURE_COLLECTION_PREFIX) :]
        type_slug = tail.split("/", 1)[0]
        if type_slug in _EXTENSION_TO_SOURCE_TYPE.values():
            return type_slug
    suffix = path.rsplit(".", 1)
    if len(suffix) != 2:
        return "unknown"
    return _EXTENSION_TO_SOURCE_TYPE.get(suffix[1].lower(), "unknown")


def _collection_to_source_type(collection: str | None) -> str | None:
    """Return the source-type slug for a per-type-fixtures collection.

    The per-type-fixture corpus uses collection names like
    ``per-type-fixtures/pptx``; we prefer this when present because the
    document paths inside that collection are extension-typed already
    and we want to attribute chunk sizes to the chunker-relevant type
    even if the path extension is missing.
    """
    if not collection or not collection.startswith(_PER_TYPE_FIXTURE_COLLECTION_PREFIX):
        return None
    tail = collection[len(_PER_TYPE_FIXTURE_COLLECTION_PREFIX) :]
    type_slug = tail.split("/", 1)[0]
    if type_slug in _EXTENSION_TO_SOURCE_TYPE.values():
        return type_slug
    return None


def _percentile(sorted_sizes: list[int], pct: float) -> int:
    """Return the ``pct``-th percentile of an already-sorted size list.

    Uses the nearest-rank method — picks the element at index
    ``ceil(pct * n) - 1``. Returns 0 for an empty list.
    """
    if not sorted_sizes:
        return 0
    if pct <= 0.0:
        return sorted_sizes[0]
    if pct >= 1.0:
        return sorted_sizes[-1]
    import math

    rank = max(1, math.ceil(pct * len(sorted_sizes)))
    return sorted_sizes[rank - 1]


def _summarise(source_type: str, sizes: list[int]) -> ChunkStats:
    sorted_sizes = sorted(sizes)
    n = len(sorted_sizes)
    if n == 0:
        return ChunkStats(source_type=source_type, n=0, mean=0.0, p50=0, p95=0, p99=0)
    mean = sum(sorted_sizes) / n
    return ChunkStats(
        source_type=source_type,
        n=n,
        mean=round(mean, 1),
        p50=_percentile(sorted_sizes, 0.50),
        p95=_percentile(sorted_sizes, 0.95),
        p99=_percentile(sorted_sizes, 0.99),
    )


def collect_chunk_sizes(db: sqlite3.Connection) -> dict[str, list[int]]:
    """Read every chunk row, group by source type, return raw sizes.

    Strategy: join ``content_vectors`` → ``content`` (for chunk text) →
    ``documents`` (for path + collection). Chunk size is approximated
    as ``length(content.doc) / count(*)`` per (hash) — since the
    storage layer holds the whole document body keyed by hash and the
    per-chunk text isn't recorded as a separate column. This matches
    the ParagraphFallbackChunker's actual average-chunk-size signal.

    When the canonical chunk text becomes a first-class column (ADR-028
    Track 2 ships a richer chunker telemetry table), this helper swaps
    its inner SELECT and the rest of the call chain stays.
    """
    # F63-bounded: per-tick chunk-stats CLI is operator-triggered and
    # the row count is naturally bounded by corpus size (typically <1M
    # chunks). Adding a LIMIT here would silently truncate the
    # distribution; the operator-facing alternative is to run the CLI
    # against a per-collection subset.
    cursor = db.execute(
        """
        SELECT
          d.collection AS collection,
          d.path AS path,
          length(c.doc) AS doc_len,
          COUNT(cv.hash) AS chunk_count
        FROM documents d
        JOIN content c ON c.hash = d.hash
        LEFT JOIN content_vectors cv ON cv.hash = d.hash
        WHERE d.active = 1
        GROUP BY d.hash
        """
    )
    # F63-bounded: operator-triggered diagnostic CLI; row count = active document count.
    rows = cursor.fetchall()

    by_type: dict[str, list[int]] = {}
    for row in rows:
        collection = row[0]
        path = row[1] or ""
        doc_len = int(row[2] or 0)
        chunk_count = int(row[3] or 0)
        if chunk_count == 0:
            continue
        approx_chunk_size = max(1, doc_len // chunk_count)
        source_type = _collection_to_source_type(collection) or _extension_to_source_type(path)
        by_type.setdefault(source_type, []).extend([approx_chunk_size] * chunk_count)
    return by_type


def compute_stats(by_type: dict[str, list[int]]) -> list[ChunkStats]:
    """Project the raw size lists into a list of :class:`ChunkStats`."""
    return [_summarise(stype, sizes) for stype, sizes in sorted(by_type.items())]


def render_human(stats: Iterable[ChunkStats]) -> str:
    """Render the chunk-stats output as a fixed-column text table.

    Returns the same shape ADR-028 §"Quality evaluation" #4 specifies:

        markdown    n=243   mean=482.0  p50=512   p95=720   p99=812
        pptx        n=156   mean=187.0  p50=160   p95=380   p99=520
        ...
    """
    rows = list(stats)
    if not rows:
        return (
            "No chunk-stats produced. The kairix index has no chunks to summarise.\n"
            "  fix: run `kairix embed` against your corpus first.\n"
            "  next: re-run `kairix eval chunk-stats` once embeddings exist.\n"
        )
    lines: list[str] = []
    for s in rows:
        lines.append(f"{s.source_type:11} n={s.n:<5} mean={s.mean:<7} p50={s.p50:<6} p95={s.p95:<6} p99={s.p99}")
    return "\n".join(lines) + "\n"


def emit_chunk_stats(db_path: Path, out_sink: TextIO) -> int:
    """Compute + emit per-source-type chunk-size stats. Returns exit code."""
    if not db_path.exists():
        out_sink.write(
            f"chunk-stats: kairix index not found at {db_path}.\n"
            f"  fix: run `kairix embed` to populate the index, "
            f"or pass --db-path to point at a different SQLite file.\n"
            f"  next: re-run `kairix eval chunk-stats --db-path <existing.sqlite>`.\n"
        )
        return 1
    # F77-allow: out-of-process operator-triggered diagnostic CLI; never runs inside the worker tick loop.
    db = sqlite3.connect(str(db_path))
    try:
        by_type = collect_chunk_sizes(db)
    finally:
        db.close()
    stats = compute_stats(by_type)
    out_sink.write(render_human(stats))
    return 0


__all__ = [
    "ChunkStats",
    "collect_chunk_sizes",
    "compute_stats",
    "emit_chunk_stats",
    "render_human",
]
