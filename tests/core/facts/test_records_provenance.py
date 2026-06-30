"""Unit tests for :func:`kairix.core.facts.resolve_fact_source_uri` (PLA-261).

The single read-time breadcrumb resolver shared by the ``facts_about`` read
surface and the SearchPipeline fact-federation path. Pins the three-step
fallback chain — each step must return a *resolvable* pointer so the
"100% of memory-read results carry a source_uri" SLO holds:

1. an explicitly-stored ``source_uri`` (authoritative / federated provenance);
2. else the conversation document path derived from ``conversation_id``;
3. else the ``facts://<id>`` self-pointer for legacy rows.

Marker rationale (``unit``): one pure function over the canonical
``FakeFactRecord`` from ``tests/fakes.py`` — no I/O, no external service.
F1/F2/F5 clean: the resolver is imported from the public ``kairix.core.facts``
surface and exercised through a canonical fake, no monkeypatch.

Every test was sabotage-proven (mutate prod → run → confirm fail → restore);
the proof transcripts are in the commit body.
"""

from __future__ import annotations

import pytest

from kairix.core.facts import resolve_fact_source_uri
from tests.fakes import FakeFactRecord

pytestmark = pytest.mark.unit


def test_explicit_source_uri_is_returned_verbatim() -> None:
    """A stored ``source_uri`` (e.g. a connector URI for a federated fact)
    is the authoritative breadcrumb and is returned as-is.

    Sabotage-proof (executed): in ``resolve_fact_source_uri`` change the
    first guard to ``if False:`` — the resolver skips the stored value and
    falls through to ``facts://<id>``; this assertion catches the leak.
    """
    record = FakeFactRecord(
        id="f-fed",
        entity="Acme",
        attribute="industry",
        value="widgets",
        source_uri="m365://sites/acme/doc-42",
    )
    assert resolve_fact_source_uri(record) == "m365://sites/acme/doc-42"


def test_conversation_id_resolves_to_conversation_document_path() -> None:
    """With no stored ``source_uri`` but a ``conversation_id``, the resolver
    returns the conversation markdown's document-relative path — the
    re-openable breadcrumb an agent uses to verify the fact.

    Sabotage-proof (executed): delete the ``conversation_id`` branch in
    ``resolve_fact_source_uri`` — the resolver returns ``facts://f-conv``
    instead and this assertion fails.
    """
    record = FakeFactRecord(
        id="f-conv",
        entity="Alice",
        attribute="role",
        value="founder",
        conversation_id="session-001",
    )
    assert resolve_fact_source_uri(record) == "04-Agent-Knowledge/conversations/session-001.md"


def test_blank_source_uri_falls_through_to_conversation_id() -> None:
    """A whitespace-only stored ``source_uri`` is treated as absent so the
    breadcrumb still resolves to the conversation document.

    Sabotage-proof (executed): drop the ``.strip()`` in the first guard —
    the blank string is treated as truthy and returned, so the assertion
    (which expects the conversation path) fails.
    """
    record = FakeFactRecord(
        id="f-blank",
        entity="Alice",
        attribute="role",
        value="founder",
        conversation_id="session-007",
        source_uri="   ",
    )
    assert resolve_fact_source_uri(record) == "04-Agent-Knowledge/conversations/session-007.md"


def test_legacy_record_without_provenance_falls_back_to_facts_uri() -> None:
    """A legacy fact (no ``source_uri``, no ``conversation_id``) still gets a
    non-empty pointer — the ``facts://<id>`` self-pointer — so the SLO that
    every result carries a source_uri holds even for pre-breadcrumb rows.

    Sabotage-proof (executed): change the final ``return f"facts://{record.id}"``
    to ``return ""`` — the SLO ("never empty") breaks and this assertion fails.
    """
    record = FakeFactRecord(id="f-legacy", entity="Bob", attribute="status", value="active")
    assert resolve_fact_source_uri(record) == "facts://f-legacy"


class _DuckRecord:
    """FactRecord-shaped object predating the provenance fields.

    Exposes only ``id`` — proves the resolver's ``getattr`` reads tolerate
    duck types that have neither ``source_uri`` nor ``conversation_id``.
    """

    id = "duck-9"


def test_resolver_tolerates_duck_typed_record_missing_provenance_attrs() -> None:
    """A record missing the provenance attributes entirely resolves to the
    ``facts://<id>`` fallback rather than raising ``AttributeError``.

    Sabotage-proof (executed): change ``getattr(record, "source_uri", None)``
    to ``record.source_uri`` — the duck record raises ``AttributeError`` and
    this test errors instead of asserting the fallback.
    """
    assert resolve_fact_source_uri(_DuckRecord()) == "facts://duck-9"
