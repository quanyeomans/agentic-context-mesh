"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`Memory`.

Four read-only properties on the recalled-memory value object
(``id``, ``content``, ``score``, ``metadata``). The Protocol explicitly
uses ``@property`` descriptors — they're part of the Protocol's
behavioural surface and can raise / return-empty just like normal
methods.

Failure surface per the Protocol docstring:

  * ``id`` — raises when the backing record is malformed (no id
    assigned by backend). The fake's inline ``_BrokenMemory`` exposes
    this shape.
  * ``content`` — returns_empty when the memory has been tombstoned /
    superseded — an empty string is still a valid Memory shape.
  * ``score`` — raises when the backend score cannot be rescaled to
    [0.0, 1.0] (e.g. NaN sentinel for "unscored").
  * ``metadata`` — returns_empty when the backend produced no metadata
    (the documented default).
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.protocols import Memory
from tests.fakes import FakeMemory

pytestmark = pytest.mark.contract


class _BrokenMemory:
    """Inline :class:`Memory` with raises-knobs on each property.

    Each property defaults to a sentinel value; pass a ``BaseException``
    sentinel via the per-property kwarg to flip that property to raise.
    Used to exercise the "raises" failure shape on each read.
    """

    def __init__(
        self,
        *,
        raise_id: BaseException | None = None,
        raise_score: BaseException | None = None,
    ) -> None:
        self._raise_id = raise_id
        self._raise_score = raise_score

    @property
    def id(self) -> str:
        if self._raise_id is not None:
            raise self._raise_id
        return "broken-id"

    @property
    def content(self) -> str:
        return ""

    @property
    def score(self) -> float:
        if self._raise_score is not None:
            raise self._raise_score
        return 0.0

    @property
    def metadata(self) -> dict[str, Any]:
        return {}


def test_id_raises_propagates_typed_exception() -> None:
    """``id`` raises when the backing record has no id assigned (backend
    bug, never expected in production but the contract is "raise, don't
    silently return ''").

    Sabotage proof: in ``_BrokenMemory.id`` change ``raise self._raise_id``
    to ``return ""``. Re-run: pytest.raises sees nothing. Restored.
    """
    mem = _BrokenMemory(raise_id=RuntimeError("F68-id-raises"))
    with pytest.raises(RuntimeError, match="F68-id-raises"):
        _ = mem.id


def test_content_returns_empty_when_memory_is_tombstoned() -> None:
    """``content`` may be an empty string when the memory has been
    tombstoned / superseded — an empty string is still a valid Memory
    shape per the Protocol; callers tolerate it.

    Sabotage proof: change ``FakeMemory.content`` to return
    ``self._content or '[empty]'``. Re-run: the ``== ""`` assertion
    fails because of the sentinel string. Restored.
    """
    mem: Memory = FakeMemory(id="m1", content="", score=0.5)
    assert mem.content == "", f"empty-content tombstone must round-trip as ''; got {mem.content!r}"


def test_score_raises_propagates_typed_exception() -> None:
    """``score`` raises when the backend score cannot be rescaled to
    [0.0, 1.0] — caller distinguishes "low confidence" (score=0.0) from
    "score is broken" (raise).

    Sabotage proof: in ``_BrokenMemory.score`` change
    ``raise self._raise_score`` to ``return 0.0``. Re-run:
    pytest.raises sees nothing. Restored.
    """
    mem = _BrokenMemory(raise_score=ValueError("F68-score-raises"))
    with pytest.raises(ValueError, match="F68-score-raises"):
        _ = mem.score


def test_metadata_returns_empty_when_backend_produced_no_metadata() -> None:
    """``metadata`` returns an empty dict when the backend produced
    none — callers iterate without a None check.

    Sabotage proof: in ``FakeMemory.metadata`` change ``return dict(self._metadata)``
    to ``return self._metadata or None``. Re-run: the ``== {}``
    assertion fails because we return ``None``. Restored.
    """
    mem: Memory = FakeMemory(id="m1", content="text", score=0.5)
    assert mem.metadata == {}, f"absent-metadata default must be {{}}; got {mem.metadata!r}"
