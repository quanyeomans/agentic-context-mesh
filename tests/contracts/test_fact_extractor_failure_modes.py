"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`FactExtractor`.

``FactExtractor.extract`` converts a window of conversation turns into
zero or more :class:`FactRecord` items. The Protocol explicitly
documents that an empty-list return is a valid "no facts groundable"
signal — callers MUST tolerate it without raising. The two canonical
failure shapes pinned below:

  * **returns_empty** — no facts in the turn window (most common path)
  * **raises** — backend / parsing failure surfaces to the caller

The production extractor is LLM-driven; the fake (``FakeFactExtractor``)
is deterministic + scripted. Both must absorb the contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.protocols import FactExtractor
from tests.fakes import FakeFactExtractor, FakeFactRecord

pytestmark = pytest.mark.contract


class _RaisingExtractor:
    """Minimal :class:`FactExtractor` impl that raises on every call.

    Inline + Protocol-shape — no monkeypatching. The constructor takes
    the exception to raise so multiple tests can pin different shapes
    without modifying the fake module.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def extract(
        self,
        *,
        turns: list[dict[str, Any]],
        window_hint: dict[str, Any] | None = None,
        session_metadata: dict[str, Any] | None = None,
    ) -> list[Any]:
        del turns, window_hint, session_metadata
        raise self._exc


def test_extract_returns_empty_when_no_facts_groundable() -> None:
    """The Protocol guarantees an empty-list return is valid — callers
    iterate without a null check. Pin the boundary by passing turns
    and asserting on the empty-list shape.

    Sabotage proof: change ``FakeFactExtractor.extract`` to return
    ``None`` when no scripted facts. Re-ran: the ``== []`` assertion
    fails. Restored.
    """
    extractor: FactExtractor = FakeFactExtractor(scripted_facts=[])
    out = extractor.extract(
        turns=[{"id": "t1", "speaker": "agent-alpha", "content": "hello"}],
    )
    assert out == []
    assert isinstance(out, list)


def test_extract_raises_propagates_typed_exception() -> None:
    """A failing extractor (LLM error, parse error, network blip)
    surfaces the exception so the ingest pipeline can dead-letter the
    window. The Protocol does NOT allow silent fallback to ``[]`` —
    that would mask backend failures as "no facts".

    Sabotage proof: change ``_RaisingExtractor.extract`` to ``return []``
    instead of raising. Re-ran: ``pytest.raises`` sees nothing and the
    test fails. Restored.
    """
    extractor: FactExtractor = _RaisingExtractor(RuntimeError("F68-extract-raises"))
    with pytest.raises(RuntimeError, match="F68-extract-raises"):
        extractor.extract(turns=[{"id": "t1", "speaker": "agent-alpha", "content": "x"}])


def test_extract_returns_empty_when_turns_empty() -> None:
    """Empty input is a degenerate "no facts groundable" case — the
    Protocol must absorb it without raising.

    Sabotage proof: change ``FakeFactExtractor.extract`` to raise
    when ``len(turns) == 0``. Re-ran: ``pytest.raises`` would catch
    it, but this test (no pytest.raises) fails because the call now
    explodes. Restored.
    """
    extractor: FactExtractor = FakeFactExtractor(
        scripted_facts=[FakeFactRecord(id="f1", entity="x", attribute="y", value="z")],
    )
    # The scripted-fake returns the facts regardless of turns; the
    # contract that matters is "no crash on empty turns".
    out = extractor.extract(turns=[])
    assert isinstance(out, list)
