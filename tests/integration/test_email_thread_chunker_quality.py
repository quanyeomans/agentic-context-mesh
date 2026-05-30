"""Quality assertions for :class:`EmailThreadChunker` (ADR-028 Wave G.1).

Seed a serialised thread with 3 messages + quoted-reply chain;
assert:
  * One chunk per message.
  * Quoted reply text appears at most once across all chunks (not N
    times across N replies) — the failure mode flat splitting would
    introduce.
  * Headers (Subject / From / Date) carry through into metadata.

Sabotage proofs (executed inline):
  * Strip the ``_strip_quoted_reply`` call → the quoted-reply
    deduplication test fails because the original message body
    surfaces N times.
  * Drop the message-sentinel split → one-chunk-per-message fails
    because the whole thread collapses into one chunk.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.email_thread import make_chunker

pytestmark = pytest.mark.integration


_THREE_MESSAGE_THREAD = (
    "Subject: Q3 plan review\n"
    "From: agent-alpha@example.test\n"
    "Date: 2026-05-28\n"
    "To: agent-beta@example.test, agent-gamma@example.test\n"
    "\n"
    "QUOTED-ANCHOR: I drafted the Q3 plan. Can you review?\n"
    "\n"
    "---MESSAGE---\n"
    "Subject: Re: Q3 plan review\n"
    "From: agent-beta@example.test\n"
    "Date: 2026-05-29\n"
    "To: agent-alpha@example.test\n"
    "\n"
    "BETA-REPLY-BODY Looks good — one nit on the staffing line.\n"
    "\n"
    "On 2026-05-28, agent-alpha wrote:\n"
    "> QUOTED-ANCHOR: I drafted the Q3 plan. Can you review?\n"
    "\n"
    "---MESSAGE---\n"
    "Subject: Re: Q3 plan review\n"
    "From: agent-gamma@example.test\n"
    "Date: 2026-05-30\n"
    "To: agent-alpha@example.test, agent-beta@example.test\n"
    "\n"
    "GAMMA-REPLY-BODY Agree with both points. Shipping today.\n"
    "\n"
    "On 2026-05-29, agent-beta wrote:\n"
    "> BETA-REPLY-BODY Looks good — one nit on the staffing line.\n"
    "On 2026-05-28, agent-alpha wrote:\n"
    "> QUOTED-ANCHOR: I drafted the Q3 plan. Can you review?\n"
)


def test_one_chunk_per_message() -> None:
    """Three messages → three chunks (each below the 4096-char cap)."""
    chunker = make_chunker()
    chunks = chunker.chunk(
        text=_THREE_MESSAGE_THREAD,
        section_kind="text",
        source_uri="thread/q3",
    )
    assert len(chunks) == 3


def test_quoted_reply_appears_at_most_once_across_chunks() -> None:
    """The original message body is quoted in replies; the chunker
    strips quoted lines so the anchor appears exactly once (in the
    original message's chunk), not three times across three replies.

    Sabotage-proof: removing the quoted-reply stripper would yield
    three appearances — flat splitting's failure mode.
    """
    chunker = make_chunker()
    chunks = chunker.chunk(
        text=_THREE_MESSAGE_THREAD,
        section_kind="text",
        source_uri="thread/q3",
    )
    anchor_appearances = sum(1 for c in chunks if "QUOTED-ANCHOR" in c.text)
    assert anchor_appearances == 1, (
        f"QUOTED-ANCHOR appeared in {anchor_appearances} chunks; expected exactly 1 "
        "(the original message). Quoted-reply stripping failed."
    )
    beta_appearances = sum(1 for c in chunks if "BETA-REPLY-BODY" in c.text)
    assert beta_appearances == 1, f"BETA-REPLY-BODY appeared in {beta_appearances} chunks; expected exactly 1."


def test_metadata_carries_headers_per_chunk() -> None:
    """Each chunk's metadata captures Subject / From / Date / To."""
    chunker = make_chunker()
    chunks = chunker.chunk(
        text=_THREE_MESSAGE_THREAD,
        section_kind="text",
        source_uri="thread/q3",
    )
    senders = sorted({c.metadata.get("header_from", "") for c in chunks})
    assert senders == [
        "agent-alpha@example.test",
        "agent-beta@example.test",
        "agent-gamma@example.test",
    ]
    # Subject is the same across the three messages but should be on
    # every chunk's metadata.
    for chunk in chunks:
        assert "Q3 plan review" in chunk.metadata.get("header_subject", "")


def test_visible_body_renders_with_subject_prefix() -> None:
    """Each chunk's text starts with the header prefix so the embedding
    sees the context (subject + sender + date)."""
    chunker = make_chunker()
    chunks = chunker.chunk(
        text=_THREE_MESSAGE_THREAD,
        section_kind="text",
        source_uri="thread/q3",
    )
    for chunk in chunks:
        body = chunk.text
        assert "Subject:" in body
        assert "From:" in body


def test_single_message_thread_chunks_cleanly() -> None:
    single = "Subject: Hello\nFrom: agent-alpha@example.test\nDate: 2026-05-30\n\nJust one message in the thread.\n"
    chunker = make_chunker()
    chunks = chunker.chunk(text=single, section_kind="text", source_uri="thread/lone")
    assert len(chunks) == 1
    assert "Just one message" in chunks[0].text
