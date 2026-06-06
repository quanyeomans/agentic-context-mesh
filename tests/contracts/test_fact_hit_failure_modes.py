"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`FactHit`.

``FactHit`` is a thin search-result wrapper — two ``@property``
accessors (``record`` + ``score``). The Protocol's documented failure
surface is **returns_empty** for the score (0.0 = floor / no recall
signal) and **returns_empty** for the record reference (None-shaped
hits are not allowed by the contract — the hit ALWAYS carries a
record). Both shapes are pinned below.

Fakes from :mod:`tests.fakes` are used directly — :class:`FakeFactHit`
is a Protocol-compliant stand-in.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import FactHit
from tests.fakes import FakeFactHit, FakeFactRecord

pytestmark = pytest.mark.contract


def test_record_returns_empty_when_no_match_record_provided() -> None:
    """The ``record`` accessor surfaces the underlying FactRecord
    verbatim. The "empty" boundary shape is a record with empty-string
    fields — the Protocol does NOT allow ``record`` to be None.

    Sabotage proof: change ``FakeFactHit.record`` to ``return None``.
    Re-ran: ``hit.record.id`` raises AttributeError and the test
    fails. Restored.
    """
    empty = FakeFactRecord(id="", entity="", attribute="", value="")
    hit: FactHit = FakeFactHit(record=empty, score=0.0)
    assert hit.record.id == ""
    assert hit.record is empty


def test_score_returns_empty_when_zero_relevance() -> None:
    """A score of 0.0 is the "no recall signal" floor — distinguishable
    from negative scores (forbidden by contract) and from a
    None-shaped score (also forbidden — the read accessor must
    always return a float).

    Sabotage proof: change ``FakeFactHit.score`` to clamp 0.0 to 0.5.
    Re-ran: ``== 0.0`` fails. Restored.
    """
    record = FakeFactRecord(id="f1", entity="x", attribute="y", value="z")
    hit: FactHit = FakeFactHit(record=record, score=0.0)
    assert hit.score == 0.0
    assert isinstance(hit.score, float)
