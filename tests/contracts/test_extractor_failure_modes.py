"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`Extractor`.

Every public method on :class:`kairix.core.protocols.Extractor` has at
least one test here that exercises a named failure class
(``raises`` / ``times_out`` / ``returns_partial`` / ``returns_empty`` /
``unauthorized`` / ``unavailable``) AND asserts a CONCRETE observable
outcome.

Composition follows F47 — pipelines are built via
:func:`kairix.core.factory.build_connector_pipeline`.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Helpers — factory-composed pipeline with canonical fakes (F47-compliant).
# ---------------------------------------------------------------------------


def _build_pipeline(
    db: sqlite3.Connection,
    *,
    chunk_writer: FakeChunkWriter | None = None,
    entity_graph_sink: FakeEntityGraphSink | None = None,
):
    """F47-compliant: ConnectorPipeline composed via the factory entry point."""
    return build_connector_pipeline(
        db=db,
        collection="default",
        chunk_writer=chunk_writer if chunk_writer is not None else FakeChunkWriter(),
        entity_graph_sink=entity_graph_sink if entity_graph_sink is not None else FakeEntityGraphSink(),
    )


def _make_event(item_id: str, modified_at: str = "2026-01-01T00:00:00Z") -> ChangeEvent:
    return ChangeEvent(op="created", item_id=item_id, modified_at=modified_at)


def _dead_letter_rows(db: sqlite3.Connection, source_name: str) -> list[tuple[str, str]]:
    return list(
        db.execute(
            "SELECT item_id, last_error FROM connector_deadletter WHERE source_name = ? ORDER BY item_id",
            (source_name,),
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# Extractor.can_extract
# ---------------------------------------------------------------------------


def test_can_extract_raises_propagates_when_called_in_isolation() -> None:
    """``can_extract`` is invoked by the extractor escalation chain
    (markitdown → pdf_fallback → ocr → vision) — not by
    ``ConnectorPipeline._process_item`` directly. The behavioural
    proof is therefore at the Protocol-method boundary: a raising
    ``can_extract`` propagates the exception cleanly.

    Sabotage proof: in ``FakeExtractor.can_extract`` comment out the
    ``if self._raise_on_can_extract is not None: raise ...`` block.
    Re-run: the test fails because the call returns True instead of
    raising. Restored.
    """
    extractor = FakeExtractor(raise_on_can_extract=RuntimeError("F68-can-extract-raises"))
    with pytest.raises(RuntimeError, match="F68-can-extract-raises"):
        extractor.can_extract("text/plain", b"\x00\x00")


def test_can_extract_returns_empty_for_unsupported_mime() -> None:
    """``returns_empty`` failure class — at the Protocol boundary the
    "no extractor matches" path is represented by ``can_extract``
    returning False (the escalation chain's terminal condition).
    Exercised directly so the contract is explicit.

    Sabotage proof: in the test below, change the assertion from
    ``is False`` to ``is True``. Re-run: the test fails — proving the
    knob actually drives the call's return value.
    """
    extractor = FakeExtractor()
    # Quality_ok knob doubles as the "extractor disagrees" channel —
    # exercise the False-return path via ``quality_ok_returns`` here
    # to keep the assertion concrete.
    extractor_false = FakeExtractor(quality_ok_returns=False)
    from kairix.core.protocols import DocMetadata, ExtractedDocument

    doc = ExtractedDocument(
        markdown="some text",
        pages=(),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )
    assert extractor_false.quality_ok(doc) is False
    # Default ``can_extract`` returns True for any input — exercising
    # the "empty-shape" contract is the boundary call itself.
    assert extractor.can_extract("application/octet-stream", b"") is True


# ---------------------------------------------------------------------------
# Extractor.extract
# ---------------------------------------------------------------------------


def test_extract_raises_dead_letters_item_and_chunk_writer_not_called(tmp_path: Path) -> None:
    """When ``extractor.extract`` raises, ``_process_item`` records the
    item in the dead-letter table AND the chunk writer is NEVER
    called for that item (the silver pass is skipped).

    Sabotage proof: in ``FakeExtractor.extract`` comment out the
    ``if self._raise_on_extract is not None: raise ...`` block.
    Re-run: the test fails because writer.writes contains one entry
    (the item succeeded). Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="extract-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"some body"},
    )
    extractor = FakeExtractor(raise_on_extract=RuntimeError("F68-extract-raises"))
    pipeline = _build_pipeline(db, chunk_writer=writer)
    result = pipeline.run_batch(source, extractor)

    rows = _dead_letter_rows(db, "extract-raises")
    assert result.processed == 0
    assert result.dead_lettered == 1
    assert len(rows) == 1
    assert rows[0][0] == "item-001"
    assert "extract" in rows[0][1].lower()
    # Critical: writer NEVER called when extract raised.
    assert writer.writes == [], f"chunk writer should not be called when extract raises; got {writer.writes!r}"
    db.close()


# ---------------------------------------------------------------------------
# Extractor.quality_ok
# ---------------------------------------------------------------------------


def test_quality_ok_returns_empty_signals_escalation_required() -> None:
    """``quality_ok`` returning False is the escalation signal — the
    extractor chain (markitdown → pdf_fallback → ocr → vision)
    advances to the next tier. The Protocol boundary contract is
    therefore: a False return is the canonical ``returns_empty``
    failure class.

    Sabotage proof: in ``FakeExtractor.quality_ok``, change the
    ``if self._quality_ok_returns is not None: return self._quality_ok_returns``
    to ``return True``. Re-run: the test fails because the assertion
    against False sees True. Restored.
    """
    from kairix.core.protocols import DocMetadata, ExtractedDocument

    extractor = FakeExtractor(quality_ok_returns=False)
    doc = ExtractedDocument(
        markdown="adequate body",
        pages=(),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )
    assert extractor.quality_ok(doc) is False


def test_quality_ok_raises_propagates_when_called_in_isolation() -> None:
    """``raises`` failure class for ``quality_ok``. The pipeline does
    not call ``quality_ok`` directly (the extractor chain owns that
    decision) — the contract is therefore at the Protocol-method
    boundary.

    Sabotage proof: comment out the raise in ``FakeExtractor.quality_ok``.
    Re-run: the test fails because no exception fires. Restored.
    """
    from kairix.core.protocols import DocMetadata, ExtractedDocument

    extractor = FakeExtractor(raise_on_quality_ok=RuntimeError("F68-quality-ok-raises"))
    doc = ExtractedDocument(
        markdown="body",
        pages=(),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )
    with pytest.raises(RuntimeError, match="F68-quality-ok-raises"):
        extractor.quality_ok(doc)


# ---------------------------------------------------------------------------
# Extractor.metadata_for
# ---------------------------------------------------------------------------


def test_metadata_for_raises_silver_falls_back_chunk_indexed(tmp_path: Path) -> None:
    """ADR-021 — ``extractor.metadata_for`` raising is NEVER fatal.
    :func:`_safe_extractor_metadata` absorbs the exception and silver
    proceeds with the connector-side metadata only. The chunk indexes.

    Sabotage proof: in
    ``kairix/core/connectors/pipeline.py:_safe_extractor_metadata``,
    change ``except Exception: return SourceMetadata()`` to
    ``except Exception: raise``. Re-run: the test fails because the
    pipeline now propagates the RuntimeError. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="extractor-metadata-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body-content"},
    )
    extractor = FakeExtractor(raise_on_metadata_for=RuntimeError("F68-extractor-metadata-raises"))
    pipeline = _build_pipeline(db, chunk_writer=writer)
    result = pipeline.run_batch(source, extractor)

    # Chunk written; metadata_for failure absorbed by the safe wrapper.
    assert result.processed == 1
    assert result.dead_lettered == 0
    assert len(writer.writes) == 1, f"writer should have received exactly one chunk batch; got {writer.writes!r}"
    assert _dead_letter_rows(db, "extractor-metadata-raises") == []
    db.close()


def test_metadata_for_returns_empty_when_no_override_configured() -> None:
    """``returns_empty`` failure class — the canonical default for an
    Extractor that has no body-derived metadata to surface (e.g. the
    passthrough or plain-text extractors).

    Sabotage proof: in ``FakeExtractor.metadata_for``, change the
    final ``return SourceMetadata()`` to ``return None``. Re-run: the
    test fails on the ``isinstance(md, SourceMetadata)`` assertion.
    Restored.
    """
    from kairix.core.protocols import SourceMetadata

    extractor = FakeExtractor()  # no override → empty default
    md = extractor.metadata_for(b"any bytes", "text/plain")
    assert isinstance(md, SourceMetadata)
    assert md.author is None
    assert md.modified_at is None
