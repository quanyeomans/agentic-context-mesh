"""Contract tests for the typed StageContext shapes (ADR-026 A.0c pre-work).

The contexts are pure data — no behaviour to test. The contract that
matters: every context is constructible with required fields,
inherits the ``source_name`` + ``item_id`` correlation pair from the
base, and is immutable (frozen=True).

A.0c is purely additive — no production code consumes the contexts
yet. Track A main will rewire ``ConnectorPipeline._process_item`` to
build and pass these contexts to ``Stage.process``.
"""

from __future__ import annotations

import dataclasses

import pytest

from kairix.core.observability.stage_contexts import (
    BronzeContext,
    ChunkWriteContext,
    EntityBufferContext,
    ExtractContext,
    FetchContext,
    SilverContext,
    StageContext,
)
from kairix.core.protocols import (
    BronzeRef,
    Chunk,
    DocMetadata,
    EntitySignal,
    ExtractedDocument,
    RawArtefact,
    SourceMetadata,
)

pytestmark = pytest.mark.contract


def _raw() -> RawArtefact:
    return RawArtefact(raw=b"body-bytes", mime="text/plain", fetched_at="2026-01-01T00:00:00Z")


def _bronze_ref() -> BronzeRef:
    return BronzeRef(
        source_name="src",
        item_id="item-001",
        raw_path="src/item-001.bin",
        mime="text/plain",
        fetched_at="2026-01-01T00:00:00Z",
    )


def _doc() -> ExtractedDocument:
    metadata = DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None)
    return ExtractedDocument(markdown="# heading\n", pages=(), images=(), metadata=metadata, confidence=1.0)


def test_fetch_context_inherits_correlation_pair() -> None:
    """FetchContext carries only ``source_name`` + ``item_id`` — the
    fetch stage doesn't need anything else. The base's frozen-dc
    fields land on the subclass automatically.
    """
    ctx = FetchContext(source_name="src", item_id="item-001")
    assert ctx.source_name == "src"
    assert ctx.item_id == "item-001"
    assert isinstance(ctx, StageContext)


def test_bronze_context_carries_raw_artefact() -> None:
    ctx = BronzeContext(source_name="src", item_id="item-001", raw_artefact=_raw())
    assert ctx.raw_artefact.mime == "text/plain"


def test_extract_context_carries_raw_artefact() -> None:
    ctx = ExtractContext(source_name="src", item_id="item-001", raw_artefact=_raw())
    assert ctx.raw_artefact.raw == b"body-bytes"


def test_silver_context_carries_full_envelope() -> None:
    """SilverContext is the widest — it threads bronze ref + extracted
    document + per-source envelope metadata (ADR-021) + extractor
    identity (ADR-024 Bundle B). Every field must round-trip.
    """
    ctx = SilverContext(
        source_name="src",
        item_id="item-001",
        bronze_ref=_bronze_ref(),
        extracted_document=_doc(),
        source_uri="https://example.com/item-001",
        source_modified_at="2026-01-01T00:00:00Z",
        sensitivity="public",
        connector_metadata=SourceMetadata(),
        extractor_metadata=SourceMetadata(),
        extractor_name="markitdown",
        extractor_version="1.0.0",
        extraction_status="ok",
    )
    assert ctx.extractor_name == "markitdown"
    assert ctx.extraction_status == "ok"
    assert ctx.sensitivity == "public"


def test_chunk_write_context_carries_chunks_sequence() -> None:
    chunks: tuple[Chunk, ...] = ()
    ctx = ChunkWriteContext(source_name="src", item_id="item-001", chunks=chunks)
    assert ctx.chunks == ()


def test_entity_buffer_context_carries_signals_sequence() -> None:
    signals: tuple[EntitySignal, ...] = ()
    ctx = EntityBufferContext(source_name="src", item_id="item-001", entity_signals=signals)
    assert ctx.entity_signals == ()


@pytest.mark.parametrize(
    "ctx",
    [
        FetchContext(source_name="src", item_id="item-001"),
        BronzeContext(source_name="src", item_id="item-001", raw_artefact=_raw()),
        ExtractContext(source_name="src", item_id="item-001", raw_artefact=_raw()),
        ChunkWriteContext(source_name="src", item_id="item-001", chunks=()),
        EntityBufferContext(source_name="src", item_id="item-001", entity_signals=()),
    ],
)
def test_contexts_are_frozen(ctx: StageContext) -> None:
    """frozen=True is the contract: mid-stage mutation is a bug, not
    a feature. ``dataclasses.FrozenInstanceError`` is the proof the
    decorator is wired correctly.
    """
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.source_name = "mutated"  # type: ignore[misc]  # intentional: proving frozen-dc rejects mutation
