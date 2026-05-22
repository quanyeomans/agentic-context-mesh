"""Connector + ingestion orchestration tree (see ``docs/architecture/connector-ingestion-architecture.md``)."""

from __future__ import annotations

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.connectors.cursor_store import CursorStore
from kairix.core.connectors.dead_letter import DeadLetterEntry, DeadLetterStore
from kairix.core.connectors.pipeline import BatchResult, ChunkWriter, ConnectorPipeline
from kairix.core.connectors.registry import (
    ConnectorRegistry,
    ExtractorRegistry,
    iter_connectors,
    iter_extractors,
    resolve_connector,
    resolve_extractor,
)
from kairix.core.connectors.silver import DefaultSilverProcessor

__all__ = [
    "BatchResult",
    "ChunkWriter",
    "ConnectorPipeline",
    "ConnectorRegistry",
    "CursorStore",
    "DeadLetterEntry",
    "DeadLetterStore",
    "DefaultSilverProcessor",
    "ExtractorRegistry",
    "FilesystemBronzeStore",
    "iter_connectors",
    "iter_extractors",
    "resolve_connector",
    "resolve_extractor",
]
