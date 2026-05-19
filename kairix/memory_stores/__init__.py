"""Memory-backend adapters satisfying ``kairix.core.protocols.MemoryStore``.

Phase 0.2 of the mem0-vs-kairix-uplift plan
(``Architecture/decisions/2026-05-20-mem0-vs-kairix-uplift-plan.md``).

The package collects pluggable implementations of the ``MemoryStore``
Protocol so use cases (``prep``, ``search``, ``brief``) can swap
backends without code changes — only config flips. Initial residents:

- :class:`KairixNativeMemoryStore` — wraps the existing
  :class:`kairix.core.search.pipeline.SearchPipeline`, vault paradigm.
- Future (Phase 1): ``Mem0MemoryStore`` — wraps ``mem0.Memory``,
  chat paradigm. Lives under ``kairix/memory_stores/mem0_backend.py``.

The boundary contract is in ``kairix/core/protocols.py``. Contract
tests pinning Protocol conformance and round-trip semantics live in
``tests/contracts/test_memory_store_protocol.py`` and (per-backend)
``tests/memory_stores/``.
"""

from __future__ import annotations

from kairix.memory_stores.kairix_native import KairixNativeMemory, KairixNativeMemoryStore

__all__ = ["KairixNativeMemory", "KairixNativeMemoryStore"]
