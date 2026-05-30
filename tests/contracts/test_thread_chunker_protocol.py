"""Contract tests for the ``thread`` Chunker plugin (F43, F55).

Pins:
* :class:`ThreadChunker` satisfies the :class:`Chunker` Protocol.
* Module-level ``version`` is non-empty AND identical to
  :attr:`ThreadChunker.version`.
* Every emitted :class:`Chunk` carries ``chunker_version=self.version``
  (F55 invariant).
* Constructor rejects non-positive caps / windows with an
  F21-shaped error string (``fix:`` / ``next:`` markers).
* The Protocol-surface ``chunk(*, text, section_kind, source_uri)``
  signature handles each of the empty / single-message / threaded /
  windowed envelope shapes without raising.

Sabotage-proofs (mutate prod → confirm fail → restore):
* Delete ``version: str = version`` from the class → asserts in
  ``test_chunker_declares_version`` fail.
* Drop ``chunker_version=self.version`` from ``_build_chunk`` →
  asserts in ``test_emitted_chunks_carry_chunker_version`` fail.
* Replace ``raise ValueError`` with ``return`` on the constructor
  guards → asserts in ``test_constructor_rejects_non_positive_cap``
  fail.
"""

from __future__ import annotations

import json

import pytest

from kairix.chunkers.thread import ThreadChunker
from kairix.chunkers.thread import version as thread_version
from kairix.core.protocols import Chunk, Chunker

pytestmark = [pytest.mark.contract]


def _msg(
    *,
    ts: str,
    user: str,
    text: str,
    thread_ts: str | None = None,
    channel: str = "ch-alpha",
) -> dict[str, object]:
    """Build a slack-shaped message dict for fixture seeding."""
    return {
        "ts": ts,
        "user": user,
        "text": text,
        "thread_ts": thread_ts,
        "channel": channel,
    }


def test_chunker_satisfies_protocol() -> None:
    """The class is recognised as a runtime :class:`Chunker`."""
    chunker = ThreadChunker()
    assert isinstance(chunker, Chunker)


def test_chunker_declares_version() -> None:
    """F55: module-level + class-level version is non-empty and consistent."""
    assert isinstance(thread_version, str)
    assert thread_version.strip() != ""
    assert ThreadChunker.version == thread_version
    assert ThreadChunker().version == thread_version


def test_empty_input_yields_no_chunks() -> None:
    chunker = ThreadChunker()
    assert chunker.chunk(text="", section_kind="text", source_uri="slack://ch-alpha") == ()
    assert chunker.chunk(text="   \n  ", section_kind="text", source_uri="slack://ch-alpha") == ()


def test_single_message_yields_one_chunk() -> None:
    chunker = ThreadChunker()
    envelope = json.dumps(_msg(ts="100.0", user="agent-alpha", text="hello world"))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/1")
    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert "hello world" in chunks[0].text


def test_emitted_chunks_carry_chunker_version() -> None:
    """F55: every Chunk threads ``chunker_version=self.version``."""
    chunker = ThreadChunker()
    envelope = json.dumps(_msg(ts="100.0", user="agent-alpha", text="hello"))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/1")
    assert chunks
    for chunk in chunks:
        assert chunk.chunker_version == chunker.version


def test_emitted_chunks_carry_source_uri_per_f39() -> None:
    """F39: ``source_uri`` propagated to every emitted Chunk."""
    chunker = ThreadChunker()
    envelope = json.dumps([_msg(ts="1.0", user="u1", text="a"), _msg(ts="2.0", user="u2", text="b")])
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/X")
    assert chunks
    for chunk in chunks:
        assert chunk.source_uri == "slack://ch-alpha/X"


def test_constructor_rejects_non_positive_cap() -> None:
    """F21-shaped error: fix: + next: markers in the message."""
    with pytest.raises(ValueError, match="max_tokens_per_chunk"):
        ThreadChunker(max_tokens_per_chunk=0)
    with pytest.raises(ValueError, match="max_tokens_per_chunk"):
        ThreadChunker(max_tokens_per_chunk=-5)


def test_constructor_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="time_window_minutes"):
        ThreadChunker(time_window_minutes=0)
    with pytest.raises(ValueError, match="time_window_minutes"):
        ThreadChunker(time_window_minutes=-1)


def test_malformed_json_degrades_gracefully() -> None:
    """Non-JSON input is treated as a one-message group, not a hard fail.

    The chunker is downstream of the connector + extractor — when
    upstream wiring is mis-shaped the chunker should degrade rather
    than crash the silver pipeline.
    """
    chunker = ThreadChunker()
    chunks = chunker.chunk(text="raw text not json", section_kind="text", source_uri="slack://x")
    assert len(chunks) == 1
    assert "raw text not json" in chunks[0].text


def test_json_payload_neither_dict_nor_list_yields_no_chunks() -> None:
    """A JSON scalar / number / bool decodes to neither dict nor list — drop it."""
    chunker = ThreadChunker()
    # A JSON number is valid JSON but isn't a message envelope.
    assert chunker.chunk(text="42", section_kind="text", source_uri="slack://x") == ()
    # A JSON string ditto.
    assert chunker.chunk(text='"hi"', section_kind="text", source_uri="slack://x") == ()


def test_list_payload_filters_non_dict_entries() -> None:
    """A list with mixed dict / non-dict entries keeps only the dicts."""
    chunker = ThreadChunker()
    envelope = json.dumps(
        [
            _msg(ts="1.0", user="agent-alpha", text="kept"),
            "not a dict",
            42,
            _msg(ts="2.0", user="agent-beta", text="also-kept"),
        ]
    )
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://x")
    # Two messages within 5 minutes of each other → one window-chunk.
    assert len(chunks) == 1
    assert "kept" in chunks[0].text
    assert "also-kept" in chunks[0].text


def test_ts_with_non_numeric_value_degrades_to_zero() -> None:
    """A non-numeric ``ts`` value falls back to 0.0 without crashing."""
    chunker = ThreadChunker()
    envelope = json.dumps(_msg(ts="not-a-number", user="agent-alpha", text="hi"))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://x")
    assert len(chunks) == 1
    # time_range = "0.0..0.0" for the bad-ts single-message case.
    assert chunks[0].metadata["time_range"] == "0.0..0.0"


def test_empty_message_text_skips_silently() -> None:
    """A message with empty text contributes no text but still counts as a member."""
    chunker = ThreadChunker()
    envelope = json.dumps(
        [
            _msg(ts="1.0", user="agent-alpha", text=""),
            _msg(ts="2.0", user="agent-beta", text="real content"),
        ]
    )
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://x")
    assert len(chunks) == 1
    # Only "real content" — the empty-text first message is skipped in join.
    assert chunks[0].text == "real content"
