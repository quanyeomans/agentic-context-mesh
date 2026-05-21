"""Unified corpus-ingest primitives.

Shared entry point every conversational ingest path routes through
once Phases P2-P6 land — ``kairix ingest-chat``, ``SuiteRunner``,
and the LoCoMo harness. P1 lands ONLY the new module + its unit
tests; subsequent phases wire the three call sites.

Public surface:
  * :class:`SessionPayload` — one in-memory conversational session
  * :class:`IngestRequest`  — a corpus-shaped unit of work
  * :class:`IngestResult`   — counters surfaced to operators + tests
  * :func:`ingest_corpus`   — the shared orchestration primitive
"""

from __future__ import annotations

from kairix.corpus.ingest import (
    IngestRequest,
    IngestResult,
    SessionPayload,
    ingest_corpus,
)

__all__ = [
    "IngestRequest",
    "IngestResult",
    "SessionPayload",
    "ingest_corpus",
]
