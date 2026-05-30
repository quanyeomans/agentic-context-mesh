"""CalendarEventChunker — one event = one chunk (ADR-028 §"Calendar event").

One event → one chunk. Do NOT split. Title + description + attendees +
location + recurrence rule + linked-doc URIs (regex-extracted from the
description body) compose the single text block. Structured fields
(``RRULE``, ``start``, ``end``, ``duration_minutes``, attendee IDs,
``linked_docs``, ``calendar_id``) ride in :attr:`Chunk.metadata` for
filter-side retrieval.

Recurrence is metadata, NOT text-expansion per ADR-028. A recurring
"30 min sync" must NOT inflate the index with N near-duplicate chunks
that all match the same query — store the master event + RRULE once;
filter-side retrieval expands occurrences when the query is
time-anchored.

Why this shape — failure modes of flat splitting on calendar events
(ADR-028 §"Calendar event — `CalendarEventChunker`"):

* title separated from attendees (a 5-word title like ``"weekly
  product review"`` is a near-noise embedding without the project +
  attendee context),
* recurring events deduplicate poorly when each occurrence is its
  own chunk,
* embedding ``"30 min sync"`` returns useless matches across hundreds
  of standups.

Input envelope shape: a JSON document mirroring the
:class:`kairix.connectors.m365_calendar.graph_client.CalendarEventRecord`
fields (``subject``, ``start_iso``, ``end_iso``, ``location``,
``attendees``, ``organiser``, plus ``description``, ``recurrence``,
``calendar_id`` when present). Google Calendar / Apple CalDAV produce
equivalent shapes after the extractor lift.

F55 contract: ``version: str`` is declared at the module level AND on
the class, AND every emitted :class:`~kairix.core.protocols.Chunk`
threads ``chunker_version=self.version`` so the maintenance-tick
re-chunk sweep can filter the affected corpus when the chunker bumps.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from kairix.core.protocols import Chunk

#: F55-mandated module-level version declaration. Mirrors the F40 pattern
#: used by extractor plugins (see ``kairix/extractors/passthrough/__init__.py``).
#: Bump on behaviour changes that warrant re-chunking the affected corpus.
version: str = "0.1.0"

# Regex extracts http(s):// URIs from the event description body so the
# CalendarEvent chunk surfaces the linked documents in metadata for
# follow-the-link retrieval. Conservative pattern — trailing punctuation
# (commas, parens, periods, semicolons) is stripped post-match.
_URL_RE = re.compile(r"https?://[^\s<>()]+")
_TRAILING_PUNCT = ".,;:)]}"


class CalendarEventChunker:
    """One event → one chunk. No split. Recurrence is metadata only.

    Parameters
    ----------
    (none) — the chunker is stateless beyond the F55 ``version`` string.
    """

    version: str = version

    def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
        """Parse the calendar-event envelope from ``text`` and emit ONE Chunk.

        ``section_kind`` is accepted for Protocol conformance but not
        used — calendar events are uniformly event-shaped. ``source_uri``
        is propagated through to the emitted Chunk per F39.

        Returns an empty tuple when the envelope decodes to nothing
        usable (empty input, blank-subject + blank-description event).
        Per ADR-028 a recurring event STILL emits exactly one chunk —
        recurrence is metadata, not text-expansion.
        """
        # section_kind kept for Protocol conformance; calendar events are
        # uniformly event-shaped so per-section dispatch isn't load-bearing
        # here. Read it once to keep the parameter live for the F19
        # unused-params check.
        if not section_kind:
            section_kind = "text"
        del section_kind
        event = _parse_event(text)
        if event is None:
            return ()
        chunk_text = _compose_text(event)
        if not chunk_text:
            return ()
        meta = _event_metadata(event)
        return (
            _build_chunk(
                text=chunk_text,
                source_uri=source_uri,
                chunker_version=self.version,
                metadata=meta,
            ),
        )


def _parse_event(text: str) -> dict[str, Any] | None:
    """Decode the JSON envelope into a single event dict.

    Accepts either a dict (single event — the canonical shape from
    every calendar connector's ``fetch()``) or a single-element list
    (defensive accommodation for batched extractor wiring). Returns
    None for empty / malformed input.
    """
    stripped = text.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


def _compose_text(event: dict[str, Any]) -> str:
    """Build the single text block: title + description + attendees + location.

    Returns an empty string when both the subject AND the description
    are blank — there's no meaningful embedding signal in an event
    that's pure metadata (start/end with no body).
    """
    title = _stringify(event.get("subject") or event.get("title") or "")
    description = _stringify(event.get("description") or event.get("body") or "")
    location = _stringify(event.get("location") or "")
    attendees = _stringify_list(event.get("attendees"))
    if not title and not description:
        return ""
    parts: list[str] = []
    if title:
        parts.append(title)
    if description:
        parts.append(description)
    if attendees:
        parts.append(f"Attendees: {', '.join(attendees)}")
    if location:
        parts.append(f"Location: {location}")
    return "\n".join(parts)


def _event_metadata(event: dict[str, Any]) -> dict[str, str]:
    """Build the per-event metadata dict carrying the structured fields.

    Captures ``start``, ``end``, ``duration_minutes``, ``attendees``
    (comma-joined emails), ``recurrence_rule`` (RRULE-shaped string),
    ``linked_docs`` (comma-joined URIs regex-extracted from the
    description), and ``calendar_id`` so retrieval can filter by
    time-window / attendee / calendar source.

    The :class:`~kairix.core.protocols.Chunk.metadata` field is
    ``Mapping[str, str]`` (F42-frozen) — all values are stringified.
    """
    start = _stringify(event.get("start") or event.get("start_iso") or "")
    end = _stringify(event.get("end") or event.get("end_iso") or "")
    duration_minutes = _duration_minutes(start=start, end=end)
    attendees = _stringify_list(event.get("attendees"))
    recurrence_rule = _stringify(event.get("recurrence") or event.get("rrule") or "")
    calendar_id = _stringify(event.get("calendar_id") or event.get("calendarId") or "")
    description = _stringify(event.get("description") or event.get("body") or "")
    linked_docs = _extract_links(description)
    return {
        "start": start,
        "end": end,
        "duration_minutes": str(duration_minutes) if duration_minutes is not None else "",
        "attendees": ",".join(attendees),
        "recurrence_rule": recurrence_rule,
        "linked_docs": ",".join(linked_docs),
        "calendar_id": calendar_id,
    }


def _stringify(value: Any) -> str:
    """Coerce ``value`` to a stripped string (None → '')."""
    if value is None:
        return ""
    return str(value).strip()


def _stringify_list(value: Any) -> list[str]:
    """Coerce a sequence-of-strings value to a list of stripped strings.

    Accepts list / tuple / set; non-sequence input returns ``[]``.
    None and empty entries are dropped.
    """
    if not isinstance(value, (list, tuple, set)) or isinstance(value, str | bytes):
        return []
    out: list[str] = []
    for item in value:
        s = _stringify(item)
        if s:
            out.append(s)
    return out


def _duration_minutes(*, start: str, end: str) -> int | None:
    """Best-effort ISO-8601 duration in whole minutes.

    Uses ``datetime.fromisoformat`` (Python 3.11+ accepts trailing
    ``"Z"`` via the standard library) — returns ``None`` when either
    bound is empty / unparseable. Negative durations are clamped to 0
    so a backwards-dated event doesn't surface a negative-minute
    metadata value.
    """
    if not start or not end:
        return None
    try:
        from datetime import datetime

        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    delta = (end_dt - start_dt).total_seconds() / 60.0
    return max(round(delta), 0)


def _extract_links(description: str) -> Sequence[str]:
    """Regex-extract http(s)://... URIs from the event description.

    Strips trailing punctuation (``)``, ``.``, ``,`` etc.) so the
    captured URI is the link itself, not the link plus a sentence
    terminator. Deduplicates while preserving first-occurrence order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.findall(description):
        cleaned = _strip_trailing_punct(match)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _strip_trailing_punct(url: str) -> str:
    """Strip trailing sentence punctuation from a captured URL."""
    while url and url[-1] in _TRAILING_PUNCT:
        url = url[:-1]
    return url


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
