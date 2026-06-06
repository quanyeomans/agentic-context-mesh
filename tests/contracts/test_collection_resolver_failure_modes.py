"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`CollectionResolver`.

Single Protocol method ``resolve(agent, scope)`` returning
``list[str] | None``. The Protocol docstring pins the failure
behaviour: returning ``None`` means "no filter — search everything",
returning ``[]`` is equivalent. Both are observable as
``returns_empty`` failure shapes (the scope produces no concrete
collections to filter on).

A separate ``raises`` probe covers the case where the resolver fails
mid-resolution (e.g. corrupt cache) — silent fallback to ``None``
would silently widen scope to every collection.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fakes import FakeCollectionResolver

pytestmark = pytest.mark.contract


def test_resolve_returns_empty_when_scope_unknown() -> None:
    """A resolver with no mapping for the requested ``(agent, scope)``
    returns ``None`` — the documented "no filter" sentinel.

    Sabotage proof: in :meth:`FakeCollectionResolver.resolve` change
    ``return self._by_key.get(...)`` to ``return ["leaked-collection"]``.
    Re-run: the test fails because the resolver returns a list instead
    of ``None``. Restored.
    """
    resolver = FakeCollectionResolver(by_key={("agent-alpha", "memory"): ["alpha-mem"]})
    assert resolver.resolve(agent="unknown", scope="memory") is None


def test_resolve_raises_when_underlying_implementation_fails() -> None:
    """A resolver whose ``resolve`` raises must surface the exception
    — silent fallback to ``None`` would widen scope to every
    collection (a security-relevant regression).

    Sabotage proof: in ``_RaisingResolver.resolve`` change
    ``raise self._exc`` to ``return None``. Re-run: the test fails
    because no exception fires and the call returns ``None``.
    Restored.
    """

    class _RaisingResolver:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def resolve(self, agent: str | None, scope: Any) -> list[str] | None:
            del agent, scope
            raise self._exc

    resolver = _RaisingResolver(RuntimeError("F68-resolver-corrupt-cache"))
    with pytest.raises(RuntimeError, match="F68-resolver-corrupt-cache"):
        resolver.resolve(agent="agent-alpha", scope="memory")
