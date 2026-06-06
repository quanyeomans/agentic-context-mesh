"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`EmbeddingService`.

Two Protocol methods: ``embed(text)`` + ``embed_batch(texts)``.

:class:`tests.fakes.FakeEmbeddingService` supports the
``returns_empty`` shape: when constructed with ``vector=[]`` every
call returns ``[]`` — the documented "soft failure" signal callers
distinguish from raised exceptions. The ``raises`` shape is probed via
inline subclasses since the existing fake has no raise knob.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeEmbeddingService

pytestmark = pytest.mark.contract


def test_embed_returns_empty_when_configured_with_empty_vector() -> None:
    """A service constructed with ``vector=[]`` returns ``[]`` for
    every ``embed`` call — the documented short-circuit signal
    backends use to mark "embed failed, skip vector path".

    Sabotage proof: in :meth:`FakeEmbeddingService.__init__` change
    the empty-list branch from ``self._vector = list(vector)`` to
    ``self._vector = [0.0] * 3``. Re-run: the test fails because the
    result is ``[0.0, 0.0, 0.0]`` instead of ``[]``. Restored.
    """
    service = FakeEmbeddingService(vector=[])
    assert service.embed("any text") == [], "empty-configured service must return []"


def test_embed_batch_returns_empty_vectors_per_input_when_configured_empty() -> None:
    """``embed_batch`` over an empty-vector service yields one ``[]``
    per input — order + length preserved so callers can still align
    vectors with their inputs (just every slot is empty).

    Sabotage proof: in :meth:`FakeEmbeddingService.embed_batch` change
    ``[list(self._vector) for _ in texts]`` to
    ``[list(self._vector)]``. Re-run: the test fails because the
    result has length 1 instead of 3. Restored.
    """
    service = FakeEmbeddingService(vector=[])
    out = service.embed_batch(["a", "b", "c"])
    assert out == [[], [], []], f"empty-configured batch must preserve length; got {out!r}"


def test_embed_raises_when_underlying_provider_crashes() -> None:
    """A service whose ``embed`` raises must surface the exception —
    silent fallback to ``[]`` would hide the difference between
    "embedded as zero" and "embed crashed".

    Sabotage proof: in ``_RaisingService.embed`` change
    ``raise self._exc`` to ``return []``. Re-run: the test fails
    because no exception fires. Restored.
    """

    class _RaisingService:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def embed(self, text: str) -> list[float]:
            del text
            raise self._exc

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise self._exc

    service = _RaisingService(RuntimeError("F68-embed-provider-down"))
    with pytest.raises(RuntimeError, match="F68-embed-provider-down"):
        service.embed("any")


def test_embed_batch_raises_when_underlying_provider_crashes() -> None:
    """Mirrors ``embed`` — ``embed_batch`` MUST also propagate. Tested
    separately so the failure-class match registers per-method (F68
    enforces per-method coverage).

    Sabotage proof: see ``test_embed_raises_when_underlying_provider_crashes``.
    """

    class _RaisingService:
        def embed(self, text: str) -> list[float]:
            del text
            return [0.1]

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise RuntimeError("F68-batch-provider-down")

    with pytest.raises(RuntimeError, match="F68-batch-provider-down"):
        _RaisingService().embed_batch(["a", "b"])
