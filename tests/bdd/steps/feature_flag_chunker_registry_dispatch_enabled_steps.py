"""Step definitions for feature_flag_chunker_registry_dispatch_enabled.feature (ADR-028).

OFF branch: Silver is built with ``chunker_registry=None`` -> the paragraph
fallback stamps ``silver-markdown-v1``. ON branch: Silver is built with
``build_default_registry()`` -> obsidian markdown dispatches to the registered
MarkdownStructuralChunker, stamping its per-type version.

F1-clean: no @patch / module-attribute substitution on kairix internals.
F2-clean: no ``KAIRIX_*`` env-var manipulation — flag state comes from
:class:`FakeFeatureFlagResolver`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, then, when

from kairix.core.connectors.chunker_registry import MARKDOWN_MIME, build_default_registry
from kairix.core.connectors.silver import SILVER_MARKDOWN_CHUNKER_VERSION, DefaultSilverProcessor
from kairix.core.protocols import BronzeRef, Chunk, DocMetadata, ExtractedDocument
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG = "chunker_registry_dispatch_enabled"


@dataclass
class _State:
    read_flag: Callable[[str], bool] | None = None
    chunks: tuple[Chunk, ...] = field(default_factory=tuple)


@pytest.fixture
def chunker_dispatch_state() -> _State:
    return _State()


def _bronze() -> BronzeRef:
    return BronzeRef(
        source_name="obsidian",
        item_id="note-1",
        raw_path=None,
        mime=MARKDOWN_MIME,
        fetched_at="2026-06-25T00:00:00Z",
    )


def _doc() -> ExtractedDocument:
    return ExtractedDocument(
        markdown="# Title\n\nA passthrough markdown paragraph for chunking.",
        pages=(),
        images=(),
        metadata=DocMetadata(title="Title", author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )


@given("an obsidian markdown document to chunk")
def _given_doc(chunker_dispatch_state: _State) -> None:
    # Constructed per-process in the @when step; nothing to stage here.
    assert chunker_dispatch_state.chunks == ()


@given("the chunker_registry_dispatch_enabled flag is OFF")
def _flag_off(chunker_dispatch_state: _State) -> None:
    chunker_dispatch_state.read_flag = FakeFeatureFlagResolver().with_flag(_FLAG, False).get


@given("the chunker_registry_dispatch_enabled flag is ON")
def _flag_on(chunker_dispatch_state: _State) -> None:
    chunker_dispatch_state.read_flag = FakeFeatureFlagResolver().with_flag(_FLAG, True).get


@when("the document is processed by Silver")
def _process(chunker_dispatch_state: _State) -> None:
    assert chunker_dispatch_state.read_flag is not None
    registry = build_default_registry() if chunker_dispatch_state.read_flag(_FLAG) else None
    silver = DefaultSilverProcessor(chunker_registry=registry)
    out = silver.process(_bronze(), _doc(), "src://obsidian/note-1", "2026-06-25T00:00:00Z", "internal")
    chunker_dispatch_state.chunks = out.chunks


@then("the chunks carry the silver-markdown fallback version")
def _then_fallback(chunker_dispatch_state: _State) -> None:
    assert chunker_dispatch_state.chunks
    assert all(c.chunker_version == SILVER_MARKDOWN_CHUNKER_VERSION for c in chunker_dispatch_state.chunks)


@then("the chunks carry a per-type chunker version")
def _then_per_type(chunker_dispatch_state: _State) -> None:
    assert chunker_dispatch_state.chunks
    assert all(c.chunker_version != SILVER_MARKDOWN_CHUNKER_VERSION for c in chunker_dispatch_state.chunks)


__all__ = ["chunker_dispatch_state"]
