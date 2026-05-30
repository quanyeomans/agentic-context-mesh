"""Contract tests for :class:`EmailThreadChunker` (ADR-028 Wave G.1).

Pins:
  * Plugin instance satisfies the
    :class:`kairix.core.protocols.Chunker` runtime-checkable Protocol.
  * Plugin declares non-empty ``version`` + ``name`` attributes (F55).
  * Every emitted :class:`Chunk` carries ``chunker_version=`` matching
    the plugin instance's version (F55).
  * Empty / whitespace-only input emits no chunks.
  * Headers (``Subject`` / ``From`` / ``Date``) surface into
    ``Chunk.metadata`` under stable keys.

Sabotage proofs (executed inline):
  * F55 carry-through: a Chunk constructed without chunker_version
    trips the assertion shape used in
    ``test_emitted_chunks_carry_plugin_version``.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.email_thread import (
    EmailThreadChunker,
    make_chunker,
    version,
)
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


_SINGLE_MESSAGE = """\
Subject: Catch-up
From: agent-alpha@example.test
Date: 2026-05-30
To: agent-beta@example.test

Hello agent-beta, are you free for a quick sync?
"""


def test_plugin_satisfies_chunker_protocol() -> None:
    chunker = make_chunker()
    assert isinstance(chunker, Chunker)
    assert chunker.name == "email_thread"
    assert chunker.version == version
    assert chunker.version  # non-empty (F55)


def test_factory_returns_real_class() -> None:
    chunker = make_chunker()
    assert isinstance(chunker, EmailThreadChunker)


def test_emitted_chunks_carry_plugin_version() -> None:
    """Every Chunk carries chunker_version=self.version (F55).

    Sabotage-proof executed: a Chunk built without chunker_version
    proves the assertion would fail if the plugin stopped threading
    the version through.
    """
    chunker = EmailThreadChunker(version="email-v3")
    chunks = chunker.chunk(text=_SINGLE_MESSAGE, section_kind="text", source_uri="msg/1")
    assert chunks
    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.chunker_version == "email-v3"

    sabotaged = Chunk(
        text="z",
        content_hash="h",
        source_name="",
        source_uri="msg/1",
        source_modified_at="",
        source_page=None,
        sensitivity="internal",
    )
    assert sabotaged.chunker_version != "email-v3"


def test_empty_input_emits_no_chunks() -> None:
    chunker = make_chunker()
    assert chunker.chunk(text="", section_kind="text", source_uri="x") == ()
    assert chunker.chunk(text="   \n  ", section_kind="text", source_uri="x") == ()


def test_headers_surface_into_metadata() -> None:
    """Subject / From / Date / To get surfaced under ``header_*`` keys."""
    chunker = make_chunker()
    chunks = chunker.chunk(text=_SINGLE_MESSAGE, section_kind="text", source_uri="msg/1")
    assert len(chunks) == 1
    metadata = chunks[0].metadata
    assert metadata.get("header_subject") == "Catch-up"
    assert metadata.get("header_from") == "agent-alpha@example.test"
    assert metadata.get("header_date") == "2026-05-30"
    assert metadata.get("header_to") == "agent-beta@example.test"


def test_emitted_chunks_propagate_source_uri() -> None:
    chunker = make_chunker()
    chunks = chunker.chunk(text=_SINGLE_MESSAGE, section_kind="text", source_uri="thread/42")
    assert chunks
    for chunk in chunks:
        assert chunk.source_uri == "thread/42"


def test_chunk_method_returns_tuple_not_list() -> None:
    chunker = make_chunker()
    assert isinstance(
        chunker.chunk(text=_SINGLE_MESSAGE, section_kind="text", source_uri="x"),
        tuple,
    )
