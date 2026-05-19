"""Memory-backend adapters satisfying ``kairix.core.protocols.MemoryStore``.

Phase 0.2 + 0.4 of the mem0-vs-kairix-uplift plan
(``Architecture/decisions/2026-05-20-mem0-vs-kairix-uplift-plan.md``).

The package collects pluggable implementations of the ``MemoryStore``
Protocol so use cases (``prep``, ``search``, ``brief``) can swap
backends without code changes — only config flips. Initial residents:

- :class:`KairixNativeMemoryStore` — wraps the existing
  :class:`kairix.core.search.pipeline.SearchPipeline`, vault paradigm.
- Future (Phase 1+): ``Mem0MemoryStore`` — wraps ``mem0.Memory``,
  chat paradigm. Lives under ``kairix/memory_stores/mem0_backend.py``
  once the LoCoMo decision-grade benchmark is in.

The boundary contract is in ``kairix/core/protocols.py``. Contract
tests pinning Protocol conformance and round-trip semantics live in
``tests/contracts/test_memory_store_protocol.py`` and (per-backend)
``tests/memory_stores/``.

:func:`make_memory_store` is the operator-facing factory — it reads a
``memory_backend:`` config field and returns the configured backend.
Default is ``kairix-native``, preserving existing operator behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kairix.core.protocols import MemoryStore
from kairix.memory_stores.kairix_native import KairixNativeMemory, KairixNativeMemoryStore

__all__ = [
    "KAIRIX_NATIVE_BACKEND",
    "MEM0_BACKEND",
    "KairixNativeMemory",
    "KairixNativeMemoryStore",
    "make_memory_store",
]


#: Canonical backend identifier for the vault-paradigm kairix-native store.
KAIRIX_NATIVE_BACKEND = "kairix-native"

#: Canonical backend identifier for the mem0 store (Phase-1 implementation
#: lives in ``scripts/benchmarks/locomo_spike.py``; promotion to a kairix
#: production backend lands once the LoCoMo benchmark validates Plan A).
MEM0_BACKEND = "mem0"


def _build_kairix_native(**kwargs: Any) -> MemoryStore:
    """Construct a :class:`KairixNativeMemoryStore` from explicit kwargs.

    Tests pass ``pipeline`` + ``paths`` directly; production callers will
    wire ``build_search_pipeline()`` + ``KairixPaths.resolve()`` at the
    caller layer once Plan A / Plan B execution lands.
    """
    return KairixNativeMemoryStore(**kwargs)


#: Backend-name → factory dispatch. Adding a new backend = one entry here
#: + a `tests/memory_stores/test_<backend>.py` contract test pinning
#: Protocol conformance + round-trip semantics.
_BACKEND_FACTORIES: dict[str, Callable[..., MemoryStore]] = {
    KAIRIX_NATIVE_BACKEND: _build_kairix_native,
}


def make_memory_store(backend: str = KAIRIX_NATIVE_BACKEND, **kwargs: Any) -> MemoryStore:
    """Construct the configured ``MemoryStore`` for an engagement / install.

    ``backend`` is the canonical name (``kairix-native``, ``mem0``, ...).
    ``**kwargs`` flow through to the chosen backend's constructor —
    each backend defines its own DI surface, so callers wire only the
    args their selected backend needs.

    Raises ``ValueError`` for unknown backends, with a clear error
    message listing what IS available — keeps operator typos cheap to
    diagnose (matches the F21 actionable-feedback pattern).
    """
    factory = _BACKEND_FACTORIES.get(backend)
    if factory is None:
        available = sorted(_BACKEND_FACTORIES)
        raise ValueError(
            f"unknown memory_backend {backend!r}; available: {available}. "
            f"fix: set memory_backend: <name> in kairix.config.yaml to one of those. "
            f"next: see Architecture/decisions/2026-05-20-mem0-vs-kairix-uplift-plan.md "
            f"for the pluggable-backend rationale."
        )
    return factory(**kwargs)
