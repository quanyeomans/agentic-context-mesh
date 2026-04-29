"""
kairix.core.temporal — Phase 2 date-aware chunking and query rewriting.

Modules:
  chunker   — Transform Kanban boards and daily memory logs into TemporalChunk objects
  rewriter  — Extract time windows from temporal queries; rewrite for BM25/vector search
  index     — Date-range query over board + memory files with BM25 scoring
  cli       — `kairix timeline` subcommand
"""

from kairix.core.temporal.chunker import TemporalChunk, chunk_board, chunk_file, chunk_memory_log

__all__ = [
    "TemporalChunk",
    "chunk_board",
    "chunk_file",
    "chunk_memory_log",
]
