"""Status codes for the pipeline observability surface (ADR-025 §4 Pattern A).

The wire format is the enum **name** (``EXTRACT_DISK_FULL``), never the
message text. Free-text ``detail`` fields are allowed for human context
but never participate in dispatch, retry decisions, or agent affordance
lookups (P2).

Each code carries a default ``Severity`` and a ``retry_eligible`` flag.
A call site cannot override either — this makes dashboards and
self-healing logic stable across pipeline refactors (P3).
"""

from __future__ import annotations

from enum import Enum


class Severity(Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


class StatusCode(Enum):
    """Per-stage status codes. Tuple value: (stage, severity, retry_eligible)."""

    # Fetch stage — bronze write
    FETCH_OK = ("fetch", Severity.OK, False)
    FETCH_TIMEOUT = ("fetch", Severity.ERROR, True)
    FETCH_THROTTLED = ("fetch", Severity.WARN, True)
    FETCH_NOT_FOUND = ("fetch", Severity.WARN, False)
    FETCH_FORBIDDEN = ("fetch", Severity.ERROR, False)
    FETCH_ZERO_BYTES = ("fetch", Severity.WARN, False)

    # Extract stage — bronze → markdown
    EXTRACT_OK = ("extract", Severity.OK, False)
    EXTRACT_OK_EMPTY = ("extract", Severity.WARN, False)
    EXTRACT_OUTPUT_EMPTY = ("extract", Severity.WARN, False)
    EXTRACT_UNSUPPORTED_MIME = ("extract", Severity.WARN, False)
    EXTRACT_DISK_FULL = ("extract", Severity.ERROR, True)
    EXTRACT_MISSING_DEPS = ("extract", Severity.ERROR, True)
    EXTRACT_CORRUPT_INPUT = ("extract", Severity.ERROR, False)

    # Silver stage — markdown → chunks
    SILVER_OK = ("silver", Severity.OK, False)
    SILVER_DEDUPED = ("silver", Severity.OK, False)
    SILVER_PRUNED_BY_MAINTENANCE = ("silver", Severity.WARN, True)
    SILVER_NO_CHUNKS_WRITTEN = ("silver", Severity.WARN, False)

    # Chunk stage — informational
    CHUNK_OK = ("chunk", Severity.OK, False)
    CHUNK_OVERSIZE_SPLIT = ("chunk", Severity.OK, False)

    # Embed stage — vectors
    EMBED_OK = ("embed", Severity.OK, False)
    EMBED_DEFERRED = ("embed", Severity.OK, False)
    EMBED_RATE_LIMITED = ("embed", Severity.WARN, True)

    # Entity stage — graph signals
    ENTITY_EXTRACTED = ("entity", Severity.OK, False)
    ENTITY_DRAIN_PENDING = ("drain", Severity.OK, False)
    ENTITY_DRAIN_PUSHED = ("drain", Severity.OK, False)
    ENTITY_DRAIN_FAILED = ("drain", Severity.ERROR, True)

    # Audit / backfill / fail-safe codes
    PRUNED_RETENTION = ("audit", Severity.OK, False)
    INFERRED_SILENT_DROP = ("audit", Severity.WARN, False)
    INFERRED_FROM_DEAD_LETTER = ("audit", Severity.WARN, False)
    PIPELINE_STAGE_NO_EMIT = ("audit", Severity.ERROR, False)

    @property
    def stage(self) -> str:
        return self.value[0]

    @property
    def severity(self) -> Severity:
        return self.value[1]

    @property
    def retry_eligible(self) -> bool:
        return self.value[2]
