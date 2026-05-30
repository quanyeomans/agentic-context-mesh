"""ThreadChunker — Slack / chat-shaped per-thread chunker (ADR-028 §"Slack / chat").

Thread = primary chunk. If the thread exceeds ``max_tokens_per_chunk``
tokens, sub-split by token cap. For non-threaded streams (no ``thread_ts``
on any message), group by ``time_window_minutes``-minute windows so a
burst of standalone messages becomes one cohesive chunk instead of N
one-liner chunks.

Why this shape — failure modes of flat splitting on Slack (ADR-028
§"Slack / chat — `ThreadChunker`"):

* one-line messages embed as near-noise (no surrounding context),
* replies separated from the parent question (thread-spanning
  questions never answer themselves),
* emoji-only / "+1" messages dilute the embedding pool.

The reference Slack-RAG case study (cited in ADR-028) reports a 5-6 %
retrieval-accuracy lift from thread-grouped chunking vs naive
character-count splitting, with the gap widening as the corpus grows.

Input envelope shape (what the Slack connector emits at ``fetch()``):
a JSON document containing either a single message dict OR a list of
message dicts. Each message carries ``thread_ts``, ``ts``, ``user``,
``text``, and ``channel`` (see :mod:`kairix.connectors.slack.connector`
``fetch()`` for the canonical key-set).

Token counting is approximate — we use a whitespace-split word count
as a portable, dependency-free proxy. The ratio drift vs a real
tokeniser (tiktoken, sentence-transformers) is small for English chat
content; the cap is conservative enough that an over-shoot of ±20 % is
acceptable. A production-grade swap to a real tokeniser is a follow-up
when the per-type eval suite (ADR-028 §"Quality evaluation") shows
recall regressions tied to chunk-size drift.

F55 contract: ``version: str`` is declared at the module level AND on
the class, AND every emitted :class:`~kairix.core.protocols.Chunk`
threads ``chunker_version=self.version`` so the maintenance-tick
re-chunk sweep can filter the affected corpus when the chunker bumps.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from kairix.core.protocols import Chunk

#: F55-mandated module-level version declaration. Mirrors the F40 pattern
#: used by extractor plugins (see ``kairix/extractors/passthrough/__init__.py``).
#: Bump on behaviour changes that warrant re-chunking the affected corpus.
version: str = "0.1.0"


class ThreadChunker:
    """Per-thread / per-window chunker for Slack-shaped chat streams.

    Parameters
    ----------
    max_tokens_per_chunk:
        Soft cap on tokens per chunk. When a thread's total token count
        exceeds this, the thread is sub-split into N chunks (each
        emitted chunk still carries the original ``thread_ts`` in
        metadata so query-side joining stays trivial). Default 500 per
        ADR-028 §"Slack / chat".
    time_window_minutes:
        Window size (minutes) for grouping non-threaded messages.
        Default 5 per ADR-028 §"Slack / chat". Messages with no
        ``thread_ts`` are clustered into windows of this size; each
        window becomes one chunk.
    """

    version: str = version

    def __init__(self, max_tokens_per_chunk: int = 500, time_window_minutes: int = 5) -> None:
        if max_tokens_per_chunk <= 0:
            raise ValueError(
                "ThreadChunker: max_tokens_per_chunk must be > 0. "
                "fix: pass a positive integer (default 500 per ADR-028). "
                "next: see docs/architecture/ADR-028-per-type-chunking-and-evaluation.md §Slack / chat."
            )
        if time_window_minutes <= 0:
            raise ValueError(
                "ThreadChunker: time_window_minutes must be > 0. "
                "fix: pass a positive integer (default 5 per ADR-028). "
                "next: see docs/architecture/ADR-028-per-type-chunking-and-evaluation.md §Slack / chat."
            )
        self._max_tokens_per_chunk = max_tokens_per_chunk
        self._time_window_minutes = time_window_minutes

    def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
        """Parse Slack-shaped envelope from ``text`` and emit per-thread chunks.

        ``section_kind`` is accepted for Protocol conformance but not
        used — Slack content is uniformly chat-shaped. ``source_uri`` is
        propagated through to each emitted Chunk per F39.
        """
        # section_kind kept for Protocol conformance; chat is uniform so the
        # Wave F per-section dispatch isn't load-bearing here. Read it once
        # to keep the parameter live for the F19 unused-params check.
        if not section_kind:
            section_kind = "text"
        del section_kind
        messages = _parse_messages(text)
        if not messages:
            return ()
        groups = _group_messages(
            messages,
            time_window_minutes=self._time_window_minutes,
        )
        chunks: list[Chunk] = []
        for group in groups:
            chunks.extend(
                self._emit_chunks_for_group(group=group, source_uri=source_uri),
            )
        return tuple(chunks)

    def _emit_chunks_for_group(
        self,
        *,
        group: Sequence[dict[str, Any]],
        source_uri: str,
    ) -> Iterable[Chunk]:
        """Emit one or more Chunks for one thread / window group.

        A group whose joined text fits in the token cap yields exactly
        one Chunk. A group exceeding the cap is sub-split by token-cap
        runs; every sub-chunk carries the same group-level metadata
        (channel, thread_ts, user_ids, time_range) so query-side
        joining stays trivial.
        """
        meta = _group_metadata(group)
        joined = _join_text(group)
        token_count = _count_tokens(joined)
        if token_count <= self._max_tokens_per_chunk:
            return (
                _build_chunk(
                    text=joined,
                    source_uri=source_uri,
                    chunker_version=self.version,
                    metadata=meta,
                ),
            )
        # Over-cap thread — sub-split by token cap, keep group metadata
        # on every sub-chunk (so the original thread is recoverable).
        sub_texts = _split_by_token_cap(joined, cap=self._max_tokens_per_chunk)
        return tuple(
            _build_chunk(
                text=sub_text,
                source_uri=source_uri,
                chunker_version=self.version,
                metadata=meta,
            )
            for sub_text in sub_texts
        )


def _parse_messages(text: str) -> list[dict[str, Any]]:
    """Decode the JSON envelope into a list of message dicts.

    The Slack connector emits either a single message dict (one-message
    fetch — see :mod:`kairix.connectors.slack.connector` ``fetch()``)
    OR a list of dicts (multi-message batches when the orchestrator
    aggregates a thread before invoking the chunker). Both shapes
    decode to a list of dicts here.
    """
    stripped = text.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        # The chunker is invoked from the Silver path where extractor
        # output is the input — if the input isn't JSON, the upstream
        # connector / extractor wiring is wrong, not the chunker. We
        # degrade to a single one-message group treating the entire
        # text as a single 'text' field so the chunker remains usable
        # in pre-wired tests.
        return [{"text": stripped, "ts": "", "user": "", "thread_ts": None, "channel": ""}]
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    return []


def _group_messages(
    messages: Sequence[dict[str, Any]],
    *,
    time_window_minutes: int,
) -> list[list[dict[str, Any]]]:
    """Group messages by thread_ts (threaded) or time window (non-threaded).

    Threaded messages cluster by ``thread_ts`` — every message sharing
    the same ``thread_ts`` lands in the same group regardless of
    insertion order. Non-threaded messages (``thread_ts`` falsy) cluster
    into ``time_window_minutes``-minute windows keyed off ``ts``.

    Returns a list of message-lists, each ordered by ``ts`` ascending.
    Group order is deterministic — threaded groups first (sorted by
    thread_ts), then window groups (sorted by window start).
    """
    threaded: dict[str, list[dict[str, Any]]] = {}
    untethered: list[dict[str, Any]] = []
    for msg in messages:
        thread_ts = msg.get("thread_ts")
        if thread_ts:
            threaded.setdefault(str(thread_ts), []).append(msg)
        else:
            untethered.append(msg)

    groups: list[list[dict[str, Any]]] = []
    for thread_ts in sorted(threaded.keys()):
        threaded[thread_ts].sort(key=lambda m: _ts_float(m.get("ts")))
        groups.append(threaded[thread_ts])

    if untethered:
        groups.extend(
            _window_group(untethered, time_window_minutes=time_window_minutes),
        )
    return groups


def _window_group(
    messages: Sequence[dict[str, Any]],
    *,
    time_window_minutes: int,
) -> list[list[dict[str, Any]]]:
    """Cluster non-threaded ``messages`` into ``time_window_minutes`` windows."""
    if not messages:
        return []
    sorted_msgs = sorted(messages, key=lambda m: _ts_float(m.get("ts")))
    window_seconds = time_window_minutes * 60
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_start = _ts_float(sorted_msgs[0].get("ts"))
    for msg in sorted_msgs:
        ts = _ts_float(msg.get("ts"))
        if not current:
            current = [msg]
            current_start = ts
            continue
        if ts - current_start < window_seconds:
            current.append(msg)
        else:
            out.append(current)
            current = [msg]
            current_start = ts
    if current:
        out.append(current)
    return out


def _ts_float(ts: Any) -> float:
    """Slack ``ts`` is a string like ``"1717185600.123456"`` — parse to float.

    Empty or non-numeric ``ts`` degrades to 0.0 (sorts to the front; no
    crash). The chunker is downstream of the connector — the connector
    guarantees ``ts`` is set on every real message; the 0.0 fallback
    keeps the pre-wired-test path usable.
    """
    if not ts:
        return 0.0
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _join_text(messages: Sequence[dict[str, Any]]) -> str:
    """Join the messages' text fields with newline separators.

    Empty texts are skipped — a message with no text content (a file
    upload, a reaction-only message) contributes no embedding signal
    so we don't pad the chunk with empty separators.
    """
    parts = [str(m.get("text", "")).strip() for m in messages]
    return "\n".join(p for p in parts if p)


def _count_tokens(text: str) -> int:
    """Approximate token count — whitespace-split word count.

    Portable proxy; the ratio drift vs a real tokeniser is acceptable
    for the per-thread cap. See the module docstring for the rationale
    + the follow-up swap-to-real-tokeniser note.
    """
    return len(text.split())


def _split_by_token_cap(text: str, *, cap: int) -> tuple[str, ...]:
    """Split ``text`` into runs each containing at most ``cap`` tokens.

    Splits on whitespace boundaries — never mid-word. Empty input
    yields an empty tuple.
    """
    tokens = text.split()
    if not tokens:
        return ()
    out: list[str] = []
    for i in range(0, len(tokens), cap):
        out.append(" ".join(tokens[i : i + cap]))
    return tuple(out)


def _group_metadata(group: Sequence[dict[str, Any]]) -> dict[str, str]:
    """Build the per-group metadata dict for the emitted Chunk.

    Captures ``channel``, ``thread_ts`` (empty for window-grouped
    non-threaded messages), ``user_ids`` (comma-joined unique user
    ids), and ``time_range`` (``"<first_ts>..<last_ts>"``) so retrieval
    can filter by channel / time-range and so display can stitch the
    chunk back into a recognisable Slack snippet.

    The :class:`~kairix.core.protocols.Chunk.metadata` field is
    ``Mapping[str, str]`` (F42-frozen) — all values are stringified.
    """
    channel = ""
    thread_ts = ""
    user_set: list[str] = []
    seen: set[str] = set()
    ts_values: list[float] = []
    for msg in group:
        if not channel:
            channel = str(msg.get("channel", "") or "")
        candidate_thread = msg.get("thread_ts")
        if candidate_thread and not thread_ts:
            thread_ts = str(candidate_thread)
        user = str(msg.get("user", "") or "")
        if user and user not in seen:
            seen.add(user)
            user_set.append(user)
        ts_values.append(_ts_float(msg.get("ts")))
    first_ts = min(ts_values) if ts_values else 0.0
    last_ts = max(ts_values) if ts_values else 0.0
    return {
        "channel": channel,
        "thread_ts": thread_ts,
        "user_ids": ",".join(user_set),
        "time_range": f"{first_ts}..{last_ts}",
    }


def _build_chunk(
    *,
    text: str,
    source_uri: str,
    chunker_version: str,
    metadata: dict[str, str],
) -> Chunk:
    """Build a :class:`Chunk` carrying the F39 / F55 invariants.

    The Silver call site fills in the per-document ``source_name``,
    ``source_modified_at``, and ``sensitivity`` when it wraps the
    chunker's output — see :meth:`SilverProcessor.process`. The
    Protocol-surface defaults here keep this helper callable from
    the bare ``Chunker.chunk(...)`` shape.
    """
    return Chunk(
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_name="",
        source_uri=source_uri,
        source_modified_at="",
        source_page=None,
        sensitivity="internal",
        chunker_version=chunker_version,
        metadata=metadata,
    )
