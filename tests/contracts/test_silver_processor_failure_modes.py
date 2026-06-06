"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`SilverProcessor`.

One method (``process``). Per the Protocol docstring + F38, Silver is
the singular chunking + entity-signal surface; failures must surface
typed exceptions so the orchestrator can route to dead-letter rather
than silently emit zero chunks (which would tombstone the document
on the next sync).

Failure surface:

  * ``returns_empty`` — empty markdown body yields a SilverOutput with
    no chunks and no signals (the documented "nothing to process" shape).
  * ``raises`` — surfaces typed exception when the inline failing
    fake's knob is flipped; pins the contract that ``process`` does
    NOT silently swallow.
"""

from __future__ import annotations

import pytest

from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.protocols import (
    BronzeRef,
    DocMetadata,
    ExtractedDocument,
    SilverOutput,
    SilverProcessor,
    SourceMetadata,
)

pytestmark = pytest.mark.contract


def _bronze_ref() -> BronzeRef:
    return BronzeRef(
        source_name="src",
        item_id="item-001",
        raw_path="src/item-001.bin",
        mime="text/plain",
        fetched_at="2026-01-01T00:00:00Z",
    )


def _empty_doc() -> ExtractedDocument:
    """Extracted document with an empty markdown body — the "nothing
    to chunk" shape."""
    return ExtractedDocument(
        markdown="",
        pages=(),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )


class _FailingSilverProcessor:
    """Inline :class:`SilverProcessor` with raises-knob on ``process``."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self._raises = raises

    def process(
        self,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        source_uri: str,
        source_modified_at: str,
        sensitivity: str,  # type: ignore[override] — tests don't import the Sensitivity Literal alias from kairix.core.protocols
        connector_metadata: SourceMetadata | None = None,
        extractor_metadata: SourceMetadata | None = None,
        extractor_name: str | None = None,
        extractor_version: str | None = None,
        extraction_status: str = "ok",
    ) -> SilverOutput:
        del (
            raw,
            extracted,
            source_uri,
            source_modified_at,
            sensitivity,
            connector_metadata,
            extractor_metadata,
            extractor_name,
            extractor_version,
            extraction_status,
        )
        if self._raises is not None:
            raise self._raises
        return SilverOutput(chunks=(), entity_signals=())


def test_process_raises_propagates_typed_exception() -> None:
    """A Silver processing failure surfaces — orchestrator must NOT
    silently emit zero chunks because that would tombstone the document
    on the next sync (the slim-prune cycle reads chunk count to decide).

    Sabotage proof: in ``_FailingSilverProcessor.process`` change
    ``raise self._raises`` to ``return SilverOutput(chunks=(), entity_signals=())``.
    Re-run: pytest.raises sees nothing. Restored.
    """
    proc: SilverProcessor = _FailingSilverProcessor(raises=RuntimeError("F68-silver-raises"))
    with pytest.raises(RuntimeError, match="F68-silver-raises"):
        proc.process(
            raw=_bronze_ref(),
            extracted=_empty_doc(),
            source_uri="https://example.com/item-001",
            source_modified_at="2026-01-01T00:00:00Z",
            sensitivity="public",  # type: ignore[arg-type] — Sensitivity is a Literal; "public" is a member but mypy narrows str
        )


def test_process_returns_empty_when_markdown_body_empty() -> None:
    """An extracted document with empty markdown yields a SilverOutput
    with no chunks and no signals — the "nothing to process" shape.

    Sabotage proof: in ``DefaultSilverProcessor.process`` change the
    empty-markdown branch to emit a phantom chunk. Re-run: the
    ``len(chunks) == 0`` assertion fails. Restored.
    """
    proc = DefaultSilverProcessor()
    out = proc.process(
        raw=_bronze_ref(),
        extracted=_empty_doc(),
        source_uri="https://example.com/item-001",
        source_modified_at="2026-01-01T00:00:00Z",
        sensitivity="public",
    )
    assert out.chunks == (), f"empty markdown must yield no chunks; got {len(out.chunks)}"
    assert out.entity_signals == (), f"empty markdown must yield no entity signals; got {len(out.entity_signals)}"
