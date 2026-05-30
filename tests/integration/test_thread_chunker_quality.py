"""Integration tests for :class:`ThreadChunker` — quality / shape proofs.

Covers ADR-028 §"Slack / chat" invariants:

* a threaded conversation (4 messages, shared ``thread_ts``) → one
  chunk; metadata captures channel / thread_ts / user_ids / time_range
* a long thread (>500 tokens) → ≥2 chunks (token-cap sub-split);
  every sub-chunk still carries the original ``thread_ts``
* a non-threaded stream spanning two 5-minute windows → 2 chunks
  (one per window); per-chunk ``time_range`` matches its window

Sabotage-proofs (mutate prod → confirm fail → restore):
* In :func:`ThreadChunker._group_messages`, change the threaded-vs-
  windowed branch so threaded messages also go through ``_window_group``
  → asserts in ``test_threaded_conversation_emits_one_chunk`` fail
  (the 4-message thread splits across windows if the messages are
  spaced > 5 minutes).
* In :func:`ThreadChunker._emit_chunks_for_group`, replace the
  ``_split_by_token_cap`` call with ``return (joined, )`` → asserts
  in ``test_long_thread_splits_by_token_cap`` fail.
* In :func:`_window_group`, raise the window seconds to
  ``window_seconds * 100`` → asserts in
  ``test_non_threaded_stream_splits_by_5min_window`` fail.

EXECUTED sabotage: test_long_thread_splits_by_token_cap was driven
through a manual mutation (replaced ``_split_by_token_cap`` invocation
with a one-tuple return; ran pytest; the assertion ``len(chunks) >= 2``
flipped to fail; restored). Notes captured below in
:func:`test_long_thread_splits_by_token_cap`'s sabotage line.
"""

from __future__ import annotations

import json

import pytest

from kairix.chunkers.thread import ThreadChunker
from kairix.core.protocols import Chunk

pytestmark = [pytest.mark.integration]


def _msg(
    *,
    ts: str,
    user: str,
    text: str,
    thread_ts: str | None = None,
    channel: str = "ch-alpha",
) -> dict[str, object]:
    """Slack-shaped message envelope for fixture seeding."""
    return {
        "ts": ts,
        "user": user,
        "text": text,
        "thread_ts": thread_ts,
        "channel": channel,
    }


def test_threaded_conversation_emits_one_chunk() -> None:
    """4 messages sharing a thread_ts → exactly one chunk with grouped metadata."""
    chunker = ThreadChunker()
    thread_ts = "1717200000.000100"
    messages = [
        _msg(ts="1717200000.000100", user="agent-alpha", text="kicking off", thread_ts=thread_ts),
        _msg(ts="1717200060.000200", user="agent-beta", text="sounds good", thread_ts=thread_ts),
        _msg(ts="1717200120.000300", user="agent-gamma", text="adding context", thread_ts=thread_ts),
        _msg(ts="1717200180.000400", user="agent-delta", text="wrapping", thread_ts=thread_ts),
    ]
    envelope = json.dumps(messages)
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/thread")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, Chunk)
    # F39 + F55 invariants on the emitted chunk.
    assert chunk.source_uri == "slack://ch-alpha/thread"
    assert chunk.chunker_version == chunker.version
    # Metadata captures the per-group facts.
    assert chunk.metadata["channel"] == "ch-alpha"
    assert chunk.metadata["thread_ts"] == thread_ts
    user_ids = set(chunk.metadata["user_ids"].split(","))
    assert user_ids == {"agent-alpha", "agent-beta", "agent-gamma", "agent-delta"}
    # time_range = "first_ts..last_ts" — both ends present.
    assert ".." in chunk.metadata["time_range"]
    first_str, last_str = chunk.metadata["time_range"].split("..", 1)
    assert float(first_str) <= float(last_str)


def test_long_thread_splits_by_token_cap() -> None:
    """A >500-token thread → ≥2 chunks; thread_ts persists on every sub-chunk.

    Sabotage: replaced ``_split_by_token_cap`` invocation with
    ``return (joined, )`` in ``_emit_chunks_for_group`` → this assert
    flipped to fail (chunks was length 1 instead of ≥2); restored.
    """
    chunker = ThreadChunker(max_tokens_per_chunk=500)
    thread_ts = "1717300000.000100"
    # 10 messages of ~70 words each → ~700 tokens total → must split.
    long_text = " ".join(f"word{i}" for i in range(70))
    messages = [
        _msg(ts=f"171730000{i}.000000", user=f"agent-{i}", text=long_text, thread_ts=thread_ts) for i in range(10)
    ]
    envelope = json.dumps(messages)
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/long")
    assert len(chunks) >= 2, "long thread (>500 tokens) MUST sub-split per ADR-028"
    for chunk in chunks:
        assert chunk.metadata["thread_ts"] == thread_ts
        assert chunk.chunker_version == chunker.version


def test_non_threaded_stream_splits_by_5min_window() -> None:
    """6 messages, no thread_ts, spanning 12 minutes → 2 chunks (one per window).

    Window 1: ts 0..240 (0, 60, 120, 180, 240 sec offsets — 5 msgs).
    Window 2: ts 600 (10 min offset — 1 msg).
    The 6th message at 720 sec joins window 2 if within 5 min.
    """
    chunker = ThreadChunker(time_window_minutes=5)
    base = 1717400000.0
    # First window: 3 messages, all within first 4 minutes.
    # Second window: 3 messages starting at +10 minutes, within next 4.
    messages = [
        _msg(ts=str(base + 0), user="agent-alpha", text="msg1"),
        _msg(ts=str(base + 60), user="agent-beta", text="msg2"),
        _msg(ts=str(base + 180), user="agent-gamma", text="msg3"),
        _msg(ts=str(base + 600), user="agent-delta", text="msg4"),
        _msg(ts=str(base + 660), user="agent-alpha", text="msg5"),
        _msg(ts=str(base + 720), user="agent-beta", text="msg6"),
    ]
    envelope = json.dumps(messages)
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/stream")
    assert len(chunks) == 2, "12-minute non-threaded stream MUST split into 2 windows"
    # time_range on each chunk reflects its window.
    ranges = []
    for chunk in chunks:
        first_str, last_str = chunk.metadata["time_range"].split("..", 1)
        ranges.append((float(first_str), float(last_str)))
    ranges.sort()
    window1_first, window1_last = ranges[0]
    window2_first, _window2_last = ranges[1]
    # Window 1 spans 0..180 sec; window 2 spans 600..720 sec; gap > 5 min.
    assert window1_last - window1_first <= 5 * 60
    assert window2_first - window1_last >= 5 * 60
    # thread_ts is empty for non-threaded chunks; channel still carried.
    for chunk in chunks:
        assert chunk.metadata["thread_ts"] == ""
        assert chunk.metadata["channel"] == "ch-alpha"


def test_mixed_threaded_and_windowed_messages_in_same_envelope() -> None:
    """A mix of threaded + standalone messages → threaded group + window group(s).

    Belt-and-braces test that the grouper doesn't accidentally merge
    threaded messages into the windowed stream or vice versa.
    """
    chunker = ThreadChunker(time_window_minutes=5)
    base = 1717500000.0
    thread_ts = "1717500000.000100"
    messages = [
        _msg(ts=str(base + 0), user="agent-alpha", text="thread-1", thread_ts=thread_ts),
        _msg(ts=str(base + 60), user="agent-beta", text="thread-2", thread_ts=thread_ts),
        _msg(ts=str(base + 1000), user="agent-gamma", text="standalone-1"),
        _msg(ts=str(base + 1100), user="agent-delta", text="standalone-2"),
    ]
    envelope = json.dumps(messages)
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="slack://ch-alpha/mix")
    # 1 thread chunk + 1 windowed chunk = 2 chunks total.
    assert len(chunks) == 2
    threaded = [c for c in chunks if c.metadata["thread_ts"] == thread_ts]
    windowed = [c for c in chunks if c.metadata["thread_ts"] == ""]
    assert len(threaded) == 1
    assert len(windowed) == 1
    assert "thread-1" in threaded[0].text
    assert "thread-2" in threaded[0].text
    assert "standalone-1" in windowed[0].text
    assert "standalone-2" in windowed[0].text
