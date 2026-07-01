"""Shared agent write-path — where a memory / conversation lands and how it is indexed.

Both surfaces that persist agent-authored markdown into the knowledge store —
``kairix remember`` / the ``memory_write`` MCP tool
(:mod:`kairix.use_cases.remember`) and ``kairix ingest-chat`` / the
``ingest_chat`` MCP tool (:mod:`kairix.use_cases.ingest_chat`) — route their
write destination and their immediate indexing through this ONE module so the
read-only-overlay fallback (PLA-296) behaves identically on both.

Two shared pieces:

- :func:`resolve_writable_memory_dir` (re-exported from :mod:`kairix.paths`) —
  prefers the ADR-017 ``04-Agent-Knowledge`` overlay, falls back to the
  writable data dir when the overlay is read-only.
- :func:`index_agent_file` — the incremental single-file index (PLA-258) that
  keeps the write BM25-searchable now, extended so a fallback write outside the
  document root is registered as an extra scan collection and indexed too.

Keeping both in one module means a future change to the fallback shape (the
collection name, the index step) has a single edit site rather than drifting
between the two callers.
"""

from __future__ import annotations

from pathlib import Path

# Re-exported so callers import the resolver + fallback helpers from the shared
# write-path module rather than reaching into kairix.paths directly.
from kairix.paths import (
    AGENT_MEMORY_FALLBACK_COLLECTION,
    ResolvedMemoryDir,
    agent_memory_fallback_root,
    resolve_writable_memory_dir,
)

__all__ = [
    "AGENT_MEMORY_FALLBACK_COLLECTION",
    "ResolvedMemoryDir",
    "agent_memory_fallback_root",
    "index_agent_file",
    "resolve_writable_memory_dir",
]


def index_agent_file(
    db_path: Path,
    document_root: Path,
    target: Path,
    content_hash: str,
    *,
    extra_scan_root: Path | None = None,
) -> bool:
    """Incrementally index the one file ``target`` just written; return searchable-now (PLA-258).

    Indexes ONLY ``target`` via
    :func:`kairix.core.embed.use_cases.default_index_file`, then reports whether
    a document with ``content_hash`` is active in the store. Reuses the
    scanner's per-file processing + an incremental FTS update, so it does NOT
    re-read or re-hash the rest of the document tree (the old full-rescan cost
    was O(corpus)). True means BM25 search finds the write now; the vector leg
    follows at the next embed run, which sees the document as pending.

    When ``extra_scan_root`` is supplied (the memory-write fallback path,
    PLA-296) an absolute ``CollectionConfig`` rooted there is registered as an
    extra scan collection under :data:`AGENT_MEMORY_FALLBACK_COLLECTION`, so a
    file written OUTSIDE the document root (into the writable data dir) still
    matches a collection and is indexed. That collection is never walked by the
    worker full-scan, so per-collection deactivation never marks the fallback
    document inactive.
    """
    from kairix.core.db import open_db
    from kairix.core.db.scanner import CollectionConfig
    from kairix.core.db.schema import create_schema
    from kairix.core.embed.use_cases import UseCaseDeps, default_index_file

    extra_collections: list[CollectionConfig] | None = None
    if extra_scan_root is not None:
        extra_collections = [CollectionConfig(name=AGENT_MEMORY_FALLBACK_COLLECTION, path=str(extra_scan_root))]

    db = open_db(db_path)
    try:
        create_schema(db)
        diagnostics: list[str] = []
        default_index_file(
            db,
            diagnostics,
            target,
            deps=UseCaseDeps(document_root_fn=lambda: document_root),
            extra_collections=extra_collections,
        )
        row = db.execute(
            "SELECT 1 FROM documents WHERE hash = ? AND active = 1 LIMIT 1",
            (content_hash,),
        ).fetchone()
        return row is not None
    finally:
        db.close()
