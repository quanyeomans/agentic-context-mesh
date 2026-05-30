"""Integration test for the ADR-028 Wave G.1 chunker registry dispatch.

Wires :func:`build_default_registry` through the
:class:`~kairix.core.connectors.chunker_registry.ChunkerRegistry`
dispatch surface and proves the right plugin resolves for each
``(kind, mime)`` Slack / calendar pair.

Sabotage-proofs:
* Remove a ``registry.register(...)`` call in :func:`build_default_registry`
  → asserts in ``test_default_registry_picks_thread_chunker_for_slack``
  OR ``test_default_registry_picks_calendar_chunker_for_*`` fail.
* Swap ``ThreadChunker()`` for ``CalendarEventChunker()`` on the slack
  entry → ``isinstance(...)`` assert flips to fail.

This test file is co-owned with W3A / W3B — additional ``(kind, mime)``
assertions for Markdown / Code / EmailThread / Slide / Sheet / Docx
land in the same file as those agents ship.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.calendar_event import CalendarEventChunker
from kairix.chunkers.thread import ThreadChunker
from kairix.core.connectors.chunker_registry import build_default_registry

pytestmark = [pytest.mark.contract]


def test_default_registry_picks_thread_chunker_for_slack_json() -> None:
    registry = build_default_registry()
    chunker = registry.dispatch(kind="slack", mime="application/json", section_kind="text")
    assert isinstance(chunker, ThreadChunker)


def test_default_registry_picks_thread_chunker_for_slack_plain() -> None:
    registry = build_default_registry()
    chunker = registry.dispatch(kind="slack", mime="text/plain", section_kind="text")
    assert isinstance(chunker, ThreadChunker)


def test_default_registry_picks_calendar_chunker_for_m365() -> None:
    registry = build_default_registry()
    chunker = registry.dispatch(kind="m365_calendar", mime="text/calendar", section_kind="text")
    assert isinstance(chunker, CalendarEventChunker)


def test_default_registry_picks_calendar_chunker_for_google() -> None:
    registry = build_default_registry()
    chunker = registry.dispatch(kind="google_calendar", mime="text/calendar", section_kind="text")
    assert isinstance(chunker, CalendarEventChunker)


def test_default_registry_picks_calendar_chunker_for_apple_caldav() -> None:
    registry = build_default_registry()
    chunker = registry.dispatch(kind="apple_caldav", mime="text/calendar", section_kind="text")
    assert isinstance(chunker, CalendarEventChunker)


def test_unknown_kind_falls_through_to_fallback() -> None:
    """An unknown ``(kind, mime)`` resolves to the paragraph fallback."""
    registry = build_default_registry()
    chunker = registry.dispatch(kind="unknown", mime="text/plain", section_kind="text")
    assert chunker is registry.fallback
