"""Connector + ingestion orchestration tree (see ``docs/architecture/connector-ingestion-architecture.md``).

Wave C additions (topology v2): cc_pair lifecycle, CollectionRouter,
ChunkerRegistry, ScopeProfileResolver, ResultEnvelope. See
``docs/architecture/connector-scope-topology/ADR.md``.
"""

from __future__ import annotations

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.connectors.cc_pair import (
    create_cc_pair,
    get_cc_pair,
    list_cc_pairs,
    transition_cc_pair,
)
from kairix.core.connectors.chunker_registry import ChunkerRegistry
from kairix.core.connectors.collection_router import CollectionRouter, RouteResult
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
from kairix.core.connectors.result_envelope import (
    ResultChunk,
    ResultEnvelope,
    SourceFreshness,
    build_envelope,
    build_freshness,
)
from kairix.core.connectors.scope_profile_resolver import (
    ExcludedCollection,
    ResolvedCollection,
    ResolvedScope,
    ScopeProfileResolver,
)
from kairix.core.connectors.silver import DefaultSilverProcessor

__all__ = [
    "BatchResult",
    "ChunkWriter",
    "ChunkerRegistry",
    "CollectionRouter",
    "ConnectorPipeline",
    "ConnectorRegistry",
    "CursorStore",
    "DeadLetterEntry",
    "DeadLetterStore",
    "DefaultSilverProcessor",
    "ExcludedCollection",
    "ExtractorRegistry",
    "FilesystemBronzeStore",
    "ResolvedCollection",
    "ResolvedScope",
    "ResultChunk",
    "ResultEnvelope",
    "RouteResult",
    "ScopeProfileResolver",
    "SourceFreshness",
    "build_envelope",
    "build_freshness",
    "create_cc_pair",
    "get_cc_pair",
    "iter_connectors",
    "iter_extractors",
    "list_cc_pairs",
    "resolve_connector",
    "resolve_extractor",
    "transition_cc_pair",
]
