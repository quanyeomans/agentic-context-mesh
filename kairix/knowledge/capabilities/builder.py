"""Feeder 1 of the capability recommender corpus.

Introspects kairix's own capability surface (the hand-maintained
``tool_capabilities()`` catalogue) and writes one capability document per
capability into the ``capabilities`` search collection — BM25- AND
vector-searchable in one pass.

This is a plain builder, not a connector: it reads the running process's own
catalogue rather than an external source, so the connector framework rules
(F34/F35/F56/F65) do not apply. It lives outside ``kairix/core/**`` so it may
import ``kairix.agents.**`` (F26 does not reach this tree).

Sabotage-proof log (executed mutate -> fail -> restore): see the test module
``tests/knowledge/capabilities/test_capability_builder.py``.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from kairix.core.protocols import Chunk, Sensitivity

logger = logging.getLogger(__name__)

_CAPABILITY_SOURCE_NAME = "kairix-capabilities"
_CHUNKER_VERSION = "capability-catalogue:v1"
_SENSITIVITY: Sensitivity = "internal"
_CAPABILITIES_COLLECTION = "capabilities"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _retrieval_document(
    *,
    name: str,
    when_to_use: str,
    description: str,
    mcp_tool: str,
    cli: str,
    category: str,
) -> str:
    """Build the retrieval document for one capability.

    Combines the name, trigger text, description, category, and invocation
    tokens so BM25 matches on the tool name AND the vector leg matches the
    trigger semantics.
    """
    lines = [name, when_to_use, description, f"category: {category}"]
    if cli:
        lines.append(f"cli: {cli}")
    if mcp_tool:
        lines.append(f"mcp tool: {mcp_tool}")
    return "\n".join(part for part in lines if part).strip() + "\n"


def build_capability_chunk(
    *,
    name: str,
    kind: str,
    surface: str,
    when_to_use: str,
    description: str,
    mcp_tool: str,
    cli: str,
    category: str,
    tick_iso: str,
) -> Chunk:
    """Render one capability into a F39-compliant :class:`Chunk`."""
    text = _retrieval_document(
        name=name,
        when_to_use=when_to_use,
        description=description,
        mcp_tool=mcp_tool,
        cli=cli,
        category=category,
    )
    return Chunk(
        text=text,
        content_hash=_hash_text(text),
        source_name=_CAPABILITY_SOURCE_NAME,
        source_uri=f"capability://kairix/{name}",
        source_modified_at=tick_iso,
        source_page=None,
        sensitivity=_SENSITIVITY,
        chunker_version=_CHUNKER_VERSION,
        tags=("capability", f"kind:{kind}", f"surface:{surface}"),
        metadata={"kind": kind, "surface": surface, "category": category},
    )


def _surface_for(mcp_tool: str | None, cli: str) -> str:
    """Derive the binding surface from the presence of MCP / CLI invocations."""
    has_mcp = bool(mcp_tool)
    has_cli = bool(cli)
    if has_mcp and has_cli:
        return "both"
    if has_mcp:
        return "mcp"
    return "cli"


def _default_catalogue() -> list[dict[str, Any]]:
    """Lazy DI default: the real kairix capability catalogue."""
    from kairix.agents.mcp.server import tool_capabilities

    return list(tool_capabilities()["capabilities"])


def _default_now() -> str:
    """Lazy DI default: the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class CapabilityCatalogueBuilder:
    """Map kairix's own ``_cap(...)`` rows into capability chunks.

    ``catalogue_fn`` returns the catalogue rows (default: the real
    ``tool_capabilities()`` surface); ``now_fn`` stamps each chunk's
    ``source_modified_at``. Both are injected in tests so the build is
    deterministic and provider-free.
    """

    catalogue_fn: Callable[[], list[dict[str, Any]]] = field(default_factory=lambda: _default_catalogue)
    now_fn: Callable[[], str] = field(default_factory=lambda: _default_now)

    def build_chunks(self) -> list[Chunk]:
        tick = self.now_fn()
        chunks: list[Chunk] = []
        for cap in self.catalogue_fn():
            name = cap["name"]
            mcp_tool = cap.get("mcp_tool") or ""
            cli = cap.get("cli", "")
            chunks.append(
                build_capability_chunk(
                    name=name,
                    kind="tool",
                    surface=_surface_for(cap.get("mcp_tool"), cli),
                    when_to_use=cap.get("when_to_use", ""),
                    description=cap.get("category", ""),
                    mcp_tool=mcp_tool,
                    cli=cli,
                    category=cap.get("category", ""),
                    tick_iso=tick,
                )
            )
        return chunks


@dataclass(frozen=True)
class CapabilityCorpusResult:
    """Outcome of a corpus build — counts plus a never-raise ``error`` channel."""

    written: int = 0
    embedded: int = 0
    error: str = ""


def _default_embed_batch(texts: list[str]) -> list[list[float]]:
    """Lazy DI default: embed ``texts`` via the production embedding service.

    Resolves the configured provider via the factory's embedding-service
    builder ON CALL (not at deps-construction) so a missing/misconfigured
    provider surfaces inside :func:`build_capability_corpus`'s never-raise
    guard rather than at ``CapabilityCorpusDeps()`` construction.
    ``_build_embedding_service`` takes the resolved :class:`RetrievalConfig`
    positionally and returns a ``ProviderEmbeddingService`` whose
    ``embed_batch(texts) -> vectors`` is the seam the corpus writer drives.
    """
    from kairix.core.factory import _build_embedding_service
    from kairix.core.search.config_loader import load_config

    svc = _build_embedding_service(load_config())
    return cast(list[list[float]], svc.embed_batch(texts))


def _default_chunk_writer(db: sqlite3.Connection) -> Any:
    """Lazy DI default: the F61-sanctioned chunk writer for ``capabilities``."""
    from kairix.core.connectors.collection_router import legacy_chunk_writer

    return legacy_chunk_writer(db, collection=_CAPABILITIES_COLLECTION)


def _default_vec_index() -> Any:
    """Lazy DI default: the canonical writable usearch index."""
    from kairix.core.embed.embed import open_default_usearch_index

    return open_default_usearch_index()


@dataclass(frozen=True)
class CapabilityCorpusDeps:
    """Injection seam for :func:`build_capability_corpus`.

    Tests pass ``embed_batch_fn=lambda texts: []`` to exercise the BM25-only
    branch with no provider; production wires the real embedding service.
    """

    builder: CapabilityCatalogueBuilder = field(default_factory=CapabilityCatalogueBuilder)
    chunk_writer_fn: Callable[[sqlite3.Connection], Any] = field(default_factory=lambda: _default_chunk_writer)
    embed_batch_fn: Callable[[list[str]], list[list[float]]] = field(default_factory=lambda: _default_embed_batch)
    vec_index_fn: Callable[[], Any] = field(default_factory=lambda: _default_vec_index)


def build_capability_corpus(
    db: sqlite3.Connection, *, deps: CapabilityCorpusDeps | None = None
) -> CapabilityCorpusResult:
    """Write every kairix capability into the ``capabilities`` collection.

    Hybrid write: chunks land in the doc store via the F61 chunk writer (BM25),
    then — unless the embed step yields empty vectors — the same chunks get
    vectors added to the usearch index (vector leg). Never raises: any failure
    surfaces via :attr:`CapabilityCorpusResult.error`.
    """
    d = deps or CapabilityCorpusDeps()
    try:
        chunks = d.builder.build_chunks()
        if not chunks:
            return CapabilityCorpusResult(error="no capabilities to index")
        writer = d.chunk_writer_fn(db)
        written = writer.upsert(chunks)
    except Exception as exc:  # never raise — surface via .error
        logger.warning("build_capability_corpus failed: %s", exc, exc_info=True)
        return CapabilityCorpusResult(error=f"{type(exc).__name__}: {exc}")

    # The vec leg is isolated from the BM25 write: a vec-index-unavailable
    # failure (read-only index, missing usearch, embed-provider down) must
    # NOT mask the successful BM25 write count (the T1 deferred minor). The
    # corpus stays BM25-searchable; the next embed/worker tick can add the
    # vectors. ``embedded`` stays 0 on a vec-leg failure but ``written`` is
    # truthfully reported.
    embedded = _embed_capabilities_safe(chunks, d)
    return CapabilityCorpusResult(written=written, embedded=embedded)


def _embed_capabilities_safe(chunks: list[Chunk], deps: CapabilityCorpusDeps) -> int:
    """Add capability vectors, isolating any vec-leg failure from the BM25 write.

    Returns 0 (and logs at WARNING with the traceback) when the vec step is
    unavailable — the BM25 write already succeeded, so the corpus is
    searchable; only the vector leg is deferred.
    """
    try:
        return _embed_capabilities(chunks, deps)
    except Exception as exc:  # vec-leg unavailable — keep the BM25 write
        # exc_info=True so the vec-leg failure's stack trace reaches the logs;
        # the BM25 write already landed, so this degrades, never fails.
        logger.warning(
            "capability corpus: vec leg unavailable; BM25 write kept — %s",
            exc,
            exc_info=True,
        )
        return 0


def _embed_capabilities(chunks: list[Chunk], deps: CapabilityCorpusDeps) -> int:
    """Add capability vectors to the usearch index — BM25-only when empty.

    Guard: when the embed step produces no vectors, a count mismatch, or a
    zero-dim first vector, skip the vec step entirely and stay BM25-only
    (do not push zero-dim vectors).
    """
    from kairix.core.embed.embed import build_hash_seq

    vectors = deps.embed_batch_fn([c.text for c in chunks])
    if not vectors or len(vectors) != len(chunks) or not vectors[0]:
        logger.info("capability corpus: no embeddings produced; BM25-only")
        return 0
    index = deps.vec_index_fn()
    hash_seqs = [build_hash_seq(c.content_hash, 0) for c in chunks]
    added = index.add_vectors(hash_seqs, vectors)
    index.save()
    return int(added)
