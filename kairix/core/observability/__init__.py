"""Pipeline observability: per-item per-stage status timeline + emit.

See ``docs/architecture/ADR-025-pipeline-observability-and-status-surface.md``
for the full spec, principles, phase gates, and definitions of done.
"""

from kairix.core.observability.remediation import REMEDIATION, Remediation
from kairix.core.observability.stage import (
    BatchTransactionStageRunner,
    IsolatedStageRunner,
    Stage,
    StageOutcome,
)
from kairix.core.observability.stage_contexts import (
    BronzeContext,
    ChunkWriteContext,
    EntityBufferContext,
    ExtractContext,
    FetchContext,
    SilverContext,
    StageContext,
)
from kairix.core.observability.status_codes import Severity, StatusCode
from kairix.core.observability.status_emit import StatusRecord, emit_for, write_status

__all__ = [
    "REMEDIATION",
    "BatchTransactionStageRunner",
    "BronzeContext",
    "ChunkWriteContext",
    "EntityBufferContext",
    "ExtractContext",
    "FetchContext",
    "IsolatedStageRunner",
    "Remediation",
    "Severity",
    "SilverContext",
    "Stage",
    "StageContext",
    "StageOutcome",
    "StatusCode",
    "StatusRecord",
    "emit_for",
    "write_status",
]
