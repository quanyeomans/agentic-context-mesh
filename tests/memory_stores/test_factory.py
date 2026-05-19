"""Contract tests for :func:`kairix.memory_stores.make_memory_store`.

Phase 0.4 of the mem0-vs-kairix-uplift plan. Pins the operator-facing
factory surface — backend selection by name, default is kairix-native,
unknown backends raise a clear ValueError with actionable feedback.

DI is F1-clean: tests pass ``pipeline`` + ``paths`` kwargs directly to
the factory (the kwargs flow through to the chosen backend's ctor).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kairix.core.protocols import MemoryStore
from kairix.memory_stores import KAIRIX_NATIVE_BACKEND, KairixNativeMemoryStore, make_memory_store
from tests.fakes import FakePaths

pytestmark = pytest.mark.contract


@dataclass
class _StubHit:
    path: str
    title: str
    snippet: str
    score: float
    tier: str = "l0"
    tokens: int = 0
    collection: str = "shared"


@dataclass
class _StubSearchResult:
    results: list[_StubHit]
    query: str = ""
    intent: Any = None
    error: str = ""


class _StubPipeline:
    """Minimal SearchPipeline stand-in so the factory can wire a real instance."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> _StubSearchResult:
        self.calls.append(dict(kwargs))
        return _StubSearchResult(results=[])


def test_factory_default_returns_kairix_native_memory_store(tmp_path) -> None:
    """No explicit backend → kairix-native (preserves operator default behaviour).

    Sabotage-proof: change ``make_memory_store``'s ``backend=`` default
    to ``"mem0"`` and the isinstance check fails because mem0 isn't
    registered + the factory raises.
    """
    store = make_memory_store(pipeline=_StubPipeline(), paths=FakePaths(document_root=tmp_path))
    assert isinstance(store, KairixNativeMemoryStore), (
        f"default backend must be kairix-native; got {type(store).__name__}"
    )


def test_factory_explicit_kairix_native_returns_kairix_native(tmp_path) -> None:
    """Explicit ``backend='kairix-native'`` returns the kairix-native store."""
    store = make_memory_store(
        backend=KAIRIX_NATIVE_BACKEND,
        pipeline=_StubPipeline(),
        paths=FakePaths(document_root=tmp_path),
    )
    assert isinstance(store, KairixNativeMemoryStore)


def test_factory_returns_memory_store_protocol_satisfier(tmp_path) -> None:
    """Whatever the factory returns satisfies the MemoryStore Protocol.

    Sabotage-proof: rename ``KairixNativeMemoryStore.delete`` and this
    runtime isinstance() probe fails.
    """
    store = make_memory_store(pipeline=_StubPipeline(), paths=FakePaths(document_root=tmp_path))
    assert isinstance(store, MemoryStore)


def test_factory_unknown_backend_raises_valueerror_with_available_list() -> None:
    """Unknown backend names produce a clear ValueError listing what IS available.

    The error message must include the unknown name, the list of valid
    names, AND F21 action markers (``fix:`` and ``next:``) so an operator
    reading the failure knows how to recover.
    """
    with pytest.raises(ValueError, match="unknown memory_backend") as exc_info:
        make_memory_store(backend="not-a-real-backend")
    msg = str(exc_info.value)
    assert "not-a-real-backend" in msg, f"error must echo the offending name; got: {msg!r}"
    assert KAIRIX_NATIVE_BACKEND in msg, "error must list what IS available"
    assert "fix:" in msg, "F21 — error must carry an action marker"
    assert "next:" in msg, "F21 — error must carry a follow-up pointer"


def test_factory_kwargs_flow_through_to_backend_constructor(tmp_path) -> None:
    """``**kwargs`` passed to make_memory_store flow into the chosen backend's ctor.

    Sabotage-proof: change the factory to swallow kwargs (e.g. ignore
    pipeline=) and the constructed store has no pipeline → search()
    fails. The probe below checks the kwargs landed.
    """
    pipeline = _StubPipeline()
    store = make_memory_store(
        backend=KAIRIX_NATIVE_BACKEND,
        pipeline=pipeline,
        paths=FakePaths(document_root=tmp_path),
    )
    # Drive the public search surface; pipeline.search must be called.
    store.search("anything")
    assert pipeline.calls, "factory must thread pipeline kwarg through to the store; got no pipeline calls"
