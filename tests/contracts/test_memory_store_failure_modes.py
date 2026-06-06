"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`MemoryStore`.

Four methods on the memory-backend surface (``add``, ``search``,
``update``, ``delete``). Per the Protocol docstring:

  * ``search`` returns ``[]`` for no matches (callers MUST tolerate it
    — empty is a valid "no relevant content" signal).
  * ``update`` raises KeyError-equivalent on missing id (the FakeStore
    surfaces ``KeyError`` directly).
  * ``delete`` is a no-op on missing id.

The ``add`` failure shape is "raises on backend write failure" —
exercised below via an inline failing fake.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fakes import FakeMemoryStore

pytestmark = pytest.mark.contract


class _FailingMemoryStore:
    """Inline :class:`MemoryStore` with a raises-knob on ``add``."""

    def __init__(self, *, raises_on_add: BaseException | None = None) -> None:
        self._raises = raises_on_add
        self._memories: dict[str, str] = {}

    def add(self, content: str, *, metadata: dict[str, Any] | None = None) -> str:
        del metadata
        if self._raises is not None:
            raise self._raises
        mem_id = f"mem-{len(self._memories) + 1:04d}"
        self._memories[mem_id] = content
        return mem_id

    def search(self, query: str, *, top_k: int = 10) -> list[Any]:
        del query, top_k
        return []

    def update(self, memory_id: str, content: str) -> None:
        del memory_id, content

    def delete(self, memory_id: str) -> None:
        del memory_id


def test_add_raises_propagates_typed_exception() -> None:
    """``add`` surfacing a backend write failure must raise — caller
    must not interpret a swallowed error as "id assigned".

    Sabotage proof: change ``_FailingMemoryStore.add`` to ``return ''``
    instead of raising. Re-run: pytest.raises sees nothing. Restored.
    """
    store = _FailingMemoryStore(raises_on_add=RuntimeError("F68-add-raises"))
    with pytest.raises(RuntimeError, match="F68-add-raises"):
        store.add("anything", metadata={})


def test_search_returns_empty_when_store_is_empty() -> None:
    """``search`` on an empty store returns ``[]`` — not None, not
    raises. Callers iterate without a null check.

    Sabotage proof: in ``FakeMemoryStore.search`` change the
    ``[m for _, m in scored[:top_k]]`` return to return ``None``
    instead of an empty list on no matches. Re-run: the ``== []``
    assertion fails. Restored.
    """
    store = FakeMemoryStore()
    out = store.search("any query", top_k=10)
    assert out == [], f"empty store must yield []; got {out!r}"


def test_update_raises_when_id_missing() -> None:
    """``update`` on a non-existent id raises KeyError per the
    Protocol docstring — the failure surface for "id absent".

    Sabotage proof: in ``FakeMemoryStore.update`` change ``raise
    KeyError(...)`` to ``return``. Re-run: pytest.raises sees nothing.
    Restored.
    """
    store = FakeMemoryStore()
    with pytest.raises(KeyError, match="no memory with id"):
        store.update("never-added-id", "new content")


def test_delete_returns_empty_when_id_missing() -> None:
    """``delete`` of a non-existent id is a no-op (idempotent) per the
    Protocol docstring. Observable: state digest unchanged.

    Sabotage proof: in ``FakeMemoryStore.delete`` change ``pop(id, None)``
    to raise KeyError on missing id. Re-run: pytest sees a raised
    exception and the test fails. Restored.
    """
    store = FakeMemoryStore()
    pre_id = store.add("existing content")
    # delete a non-existent id must absorb cleanly
    store.delete("never-added-id")
    # state unchanged: pre-existing entry still surfaces under search
    assert store.search("existing", top_k=5), (
        "delete of an absent id must not corrupt other state; pre-existing entry must still be searchable"
    )
    assert pre_id
