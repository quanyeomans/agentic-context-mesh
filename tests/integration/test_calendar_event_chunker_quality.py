"""Integration tests for :class:`CalendarEventChunker` — quality / shape proofs.

Covers ADR-028 §"Calendar event" invariants:

* one event (title + description + 3 attendees + location + RRULE + 2
  linked docs) → one chunk; chunk text carries title + description +
  attendees + location; metadata carries every structured field
* recurring event with RRULE → exactly one chunk (recurrence is
  metadata, NOT text-expansion per ADR-028)
* linked-doc extraction strips trailing punctuation + dedups

Sabotage-proofs (mutate prod → confirm fail → restore):
* In :func:`_compose_text`, drop the ``Location: ...`` append → asserts
  in ``test_event_with_full_payload`` fail.
* In :meth:`CalendarEventChunker.chunk`, return ``(chunk, chunk, chunk)``
  for recurring events (text-expansion) → asserts in
  ``test_recurring_event_is_metadata_not_text_expansion`` fail.
* In :func:`_extract_links`, drop the ``_strip_trailing_punct`` call →
  asserts in ``test_linked_docs_strip_trailing_punctuation`` fail.

EXECUTED sabotage: ``test_event_with_full_payload`` was driven through
a manual mutation (commented out the ``Location:`` append in
``_compose_text`` → ran pytest → ``assert "room-alpha" in chunk_text``
flipped to fail; restored).
"""

from __future__ import annotations

import json

import pytest

from kairix.chunkers.calendar_event import CalendarEventChunker
from kairix.core.protocols import Chunk

pytestmark = [pytest.mark.integration]


def _event(**overrides: object) -> dict[str, object]:
    """Build a representative calendar-event envelope for fixture seeding."""
    base: dict[str, object] = {
        "subject": "weekly product review",
        "description": ("kickoff agenda, link https://docs.example.com/agenda and ref https://wiki.example.com/notes."),
        "start": "2026-05-30T14:00:00+00:00",
        "end": "2026-05-30T15:00:00+00:00",
        "attendees": [
            "agent-alpha@example.com",
            "agent-beta@example.com",
            "agent-gamma@example.com",
        ],
        "location": "room-alpha",
        "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=FR",
        "calendar_id": "cal-product",
    }
    base.update(overrides)
    return base


def test_event_with_full_payload() -> None:
    """One event → one chunk; text + metadata carry every structured field."""
    chunker = CalendarEventChunker()
    envelope = json.dumps(_event())
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/wpr")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, Chunk)

    # Chunk text carries title + description + attendees + location.
    assert "weekly product review" in chunk.text
    assert "kickoff agenda" in chunk.text
    assert "agent-alpha@example.com" in chunk.text
    assert "agent-beta@example.com" in chunk.text
    assert "agent-gamma@example.com" in chunk.text
    assert "room-alpha" in chunk.text

    # Metadata carries every structured field.
    meta = chunk.metadata
    assert meta["start"] == "2026-05-30T14:00:00+00:00"
    assert meta["end"] == "2026-05-30T15:00:00+00:00"
    assert meta["duration_minutes"] == "60"
    attendees = set(meta["attendees"].split(","))
    assert attendees == {
        "agent-alpha@example.com",
        "agent-beta@example.com",
        "agent-gamma@example.com",
    }
    assert meta["recurrence_rule"] == "RRULE:FREQ=WEEKLY;BYDAY=FR"
    linked = set(meta["linked_docs"].split(","))
    assert linked == {
        "https://docs.example.com/agenda",
        "https://wiki.example.com/notes",
    }
    assert meta["calendar_id"] == "cal-product"

    # F39 + F55 invariants.
    assert chunk.source_uri == "cal://event/wpr"
    assert chunk.chunker_version == chunker.version


def test_recurring_event_is_metadata_not_text_expansion() -> None:
    """ADR-028: recurrence stays as metadata; never N chunks per occurrence."""
    chunker = CalendarEventChunker()
    envelope = json.dumps(_event(recurrence="RRULE:FREQ=DAILY;COUNT=30"))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/recurrer")
    assert len(chunks) == 1
    assert chunks[0].metadata["recurrence_rule"] == "RRULE:FREQ=DAILY;COUNT=30"


def test_event_without_recurrence_has_empty_rrule() -> None:
    chunker = CalendarEventChunker()
    envelope = json.dumps(_event(recurrence=None))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/oneoff")
    assert len(chunks) == 1
    assert chunks[0].metadata["recurrence_rule"] == ""


def test_linked_docs_strip_trailing_punctuation() -> None:
    """URLs in prose carry trailing ``.``, ``,``, ``)`` — strip them."""
    chunker = CalendarEventChunker()
    envelope = json.dumps(
        _event(
            description=(
                "see (https://docs.example.com/a), "
                "and https://docs.example.com/b. "
                "Also https://docs.example.com/a — dup; "
                "and https://docs.example.com/c; final."
            )
        )
    )
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/links")
    assert len(chunks) == 1
    links = chunks[0].metadata["linked_docs"].split(",")
    # Dedup: a appears twice in source; once in output.
    assert links == [
        "https://docs.example.com/a",
        "https://docs.example.com/b",
        "https://docs.example.com/c",
    ]


def test_event_with_no_attendees() -> None:
    """An attendee-less event still emits one chunk; ``attendees`` is empty string."""
    chunker = CalendarEventChunker()
    envelope = json.dumps(_event(attendees=[]))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/solo")
    assert len(chunks) == 1
    assert chunks[0].metadata["attendees"] == ""
    # Chunk text should NOT contain a stray "Attendees:" header.
    assert "Attendees:" not in chunks[0].text


def test_duration_minutes_handles_z_suffix() -> None:
    """ISO-8601 ``...Z`` suffix is valid input — parse it, don't crash."""
    chunker = CalendarEventChunker()
    envelope = json.dumps(
        _event(start="2026-05-30T10:00:00Z", end="2026-05-30T10:45:00Z"),
    )
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/z")
    assert chunks[0].metadata["duration_minutes"] == "45"


def test_duration_minutes_missing_when_bounds_blank() -> None:
    """Missing start/end → ``duration_minutes`` is empty (not '0')."""
    chunker = CalendarEventChunker()
    envelope = json.dumps(_event(start=None, end=None))
    chunks = chunker.chunk(text=envelope, section_kind="text", source_uri="cal://event/nobounds")
    assert chunks[0].metadata["duration_minutes"] == ""
