"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`FactRecord`.

``FactRecord`` is a pure-read Protocol (every method is a ``@property``
accessor). The Protocol's documented "failure" surface is the
**returns_empty** path for the two nullable fields (``superseded_by``
and ``evidence_at``) plus the empty-tuple shape for ``source_turn_ids``
on legacy / invalid rows.

Per F68 spec — "When the Protocol method's failure surface is
genuinely empty (rare — a pure-functional method with no I/O)":
``test_<method>_returns_empty_when_no_input_provided`` is the
canonical name. The pure-read accessors below follow that pattern
through the public ``FakeFactRecord`` boundary.

Fakes from :mod:`tests.fakes` are used directly — :class:`FakeFactRecord`
is a Protocol-compliant frozen-dataclass-shape stand-in.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import FactRecord
from tests.fakes import FakeFactRecord

pytestmark = pytest.mark.contract


def _bare_record() -> FactRecord:
    """A FactRecord constructed with only the required positional fields.

    Mirrors what a legacy-row read returns when the optional fields
    were never populated. Every "returns_empty" assertion below pins
    a specific nullable / empty-collection shape.
    """
    return FakeFactRecord(id="f1", entity="agent-alpha", attribute="role", value="VP")


def test_id_returns_empty_when_no_input_provided() -> None:
    """The Protocol's ``id`` accessor returns the supplied id verbatim
    — no transformation, no derivation. The "empty" failure shape is
    the empty string, which is a valid (but discouraged) id.

    Sabotage proof: change ``FakeFactRecord.id`` to always return
    ``"sentinel"``. Re-ran: the equality assertion fails. Restored.
    """
    record: FactRecord = FakeFactRecord(id="", entity="x", attribute="y", value="z")
    assert record.id == ""


def test_entity_returns_empty_when_no_input_provided() -> None:
    """The ``entity`` accessor surfaces the entity string verbatim."""
    record: FactRecord = FakeFactRecord(id="f1", entity="", attribute="y", value="z")
    assert record.entity == ""


def test_attribute_returns_empty_when_no_input_provided() -> None:
    """The ``attribute`` accessor surfaces the attribute string verbatim."""
    record: FactRecord = FakeFactRecord(id="f1", entity="x", attribute="", value="z")
    assert record.attribute == ""


def test_value_returns_empty_when_no_input_provided() -> None:
    """The ``value`` accessor surfaces the value string verbatim."""
    record: FactRecord = FakeFactRecord(id="f1", entity="x", attribute="y", value="")
    assert record.value == ""


def test_confidence_returns_empty_when_zero_provided() -> None:
    """``confidence`` is a float in [0.0, 1.0]; the boundary "empty"
    shape is 0.0 — pinning a fact the extractor was certain was
    wrong / unsupported.

    Sabotage proof: change ``FakeFactRecord.confidence`` to clamp 0.0
    to 0.5. Re-ran: the ``== 0.0`` assertion fails. Restored.
    """
    record: FactRecord = FakeFactRecord(id="f1", entity="x", attribute="y", value="z", confidence=0.0)
    assert record.confidence == 0.0


def test_source_turn_ids_returns_empty_when_no_input_provided() -> None:
    """Legacy rows pre-Lever-A have empty ``source_turn_ids`` tuples —
    the FactStore-side ingest validator rejects them, but the read
    accessor itself returns the empty tuple shape verbatim.

    Sabotage proof: change ``FakeFactRecord.source_turn_ids`` to
    return ``(None,)`` for empty. Re-ran: the ``== ()`` assertion fails.
    Restored.
    """
    record: FactRecord = _bare_record()
    assert record.source_turn_ids == ()


def test_extracted_at_returns_empty_when_epoch_provided() -> None:
    """The default ``extracted_at`` is the epoch sentinel — pinning a
    "no real timestamp" row shape that callers can detect.
    """
    record: FactRecord = _bare_record()
    assert record.extracted_at == "1970-01-01T00:00:00Z"


def test_superseded_by_returns_empty_when_record_is_live() -> None:
    """Live (current) facts have ``superseded_by is None`` — the
    canonical "returns_empty" shape. Callers test ``is None`` to
    filter superseded rows out of default search.

    Sabotage proof: change ``FakeFactRecord.superseded_by`` to return
    an empty string. Re-ran: ``is None`` becomes False and the test
    fails. Restored.
    """
    record: FactRecord = _bare_record()
    assert record.superseded_by is None


def test_namespace_returns_empty_when_default_provided() -> None:
    """``namespace`` defaults to ``"shared"`` — the engagement-scope-
    free shape every fact carries until explicit scoping kicks in.
    """
    record: FactRecord = _bare_record()
    assert record.namespace == "shared"


def test_evidence_at_returns_empty_when_no_temporal_anchor() -> None:
    """Pre-Lever-A legacy rows carry ``evidence_at is None`` — the
    "no event-time anchor" shape. Production code uses this exact
    null-check to decide whether to fall back to ``extracted_at``.

    Sabotage proof: change ``FakeFactRecord.evidence_at`` to return
    ``""`` for missing anchors. Re-ran: ``is None`` becomes False.
    Restored.
    """
    record: FactRecord = _bare_record()
    assert record.evidence_at is None
