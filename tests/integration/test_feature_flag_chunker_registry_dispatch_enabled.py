"""F54 integration parity for the chunker_registry_dispatch_enabled flag (ADR-028).

Pins that the flag's two branches produce observably different chunker_version
stamps through the same call site — OFF keeps ``silver-markdown-v1``; ON
dispatches obsidian markdown to the per-type MarkdownStructuralChunker.

Flag resolution uses :class:`FakeFeatureFlagResolver` (F1-clean: no @patch /
module-attribute substitution). ``_build_silver`` mirrors the production call
site (``worker._silver_with_registry``): read the flag, wire the registry or None.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.core.connectors.chunker_registry import MARKDOWN_MIME, build_default_registry
from kairix.core.connectors.silver import SILVER_MARKDOWN_CHUNKER_VERSION, DefaultSilverProcessor
from kairix.core.protocols import BronzeRef, Chunk, DocMetadata, ExtractedDocument
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG = "chunker_registry_dispatch_enabled"


def _build_silver(read_flag: Callable[[str], bool]) -> DefaultSilverProcessor:
    registry = build_default_registry() if read_flag(_FLAG) else None
    return DefaultSilverProcessor(chunker_registry=registry)


def _bronze() -> BronzeRef:
    return BronzeRef(
        source_name="obsidian",
        item_id="n",
        raw_path=None,
        mime=MARKDOWN_MIME,
        fetched_at="2026-06-25T00:00:00Z",
    )


def _doc() -> ExtractedDocument:
    return ExtractedDocument(
        markdown="# Heading\n\nbody paragraph one.\n\nbody paragraph two.",
        pages=(),
        images=(),
        metadata=DocMetadata(title="t", author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )


def _process(read_flag: Callable[[str], bool]) -> tuple[Chunk, ...]:
    return _build_silver(read_flag).process(_bronze(), _doc(), "src://x", "2026-06-25T00:00:00Z", "internal").chunks


def test_flag_off_keeps_paragraph_fallback() -> None:
    chunks = _process(FakeFeatureFlagResolver().with_flag("chunker_registry_dispatch_enabled", False).get)
    assert chunks
    assert all(c.chunker_version == SILVER_MARKDOWN_CHUNKER_VERSION for c in chunks)


def test_flag_on_dispatches_to_per_type_chunker() -> None:
    chunks = _process(FakeFeatureFlagResolver().with_flag("chunker_registry_dispatch_enabled", True).get)
    assert chunks
    assert all(c.chunker_version != SILVER_MARKDOWN_CHUNKER_VERSION for c in chunks)


def test_off_then_on_changes_chunker_version() -> None:
    off = _process(FakeFeatureFlagResolver().with_flag("chunker_registry_dispatch_enabled", False).get)
    on = _process(FakeFeatureFlagResolver().with_flag("chunker_registry_dispatch_enabled", True).get)
    assert off[0].chunker_version == SILVER_MARKDOWN_CHUNKER_VERSION
    assert on[0].chunker_version != SILVER_MARKDOWN_CHUNKER_VERSION
