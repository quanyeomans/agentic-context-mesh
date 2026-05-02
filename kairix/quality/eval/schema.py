"""Structured query log entry schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryLogEntry:
    """One search event. Written to JSONL by QueryLogger."""

    ts: str  # ISO-8601 UTC timestamp
    agent: str  # Agent name (e.g. "shape", "builder")
    query: str  # Raw query string
    intent: str  # Classified intent (e.g. "temporal", "entity")
    result_count: int  # Number of results returned
    bm25_count: int  # Results from BM25 leg
    vec_count: int  # Results from vector leg
    latency_ms: float  # Total search latency
    top_path: str | None = None  # Path of highest-scored result
    session_id: str | None = None  # OpenClaw session ID if available
    vec_failed: bool = False  # True if vector search failed (dim mismatch etc.)
    error: str | None = None  # Error string if search failed
    extra: dict[str, Any] = field(default_factory=dict)  # Any additional diagnostic fields
