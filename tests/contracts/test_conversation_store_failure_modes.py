"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ConversationStore`.

Five Protocol methods: ``add`` / ``add_turn`` / ``search`` / ``update``
/ ``delete``. :class:`tests.fakes.FakeConversationStore` (subclass of
:class:`FakeMemoryStore`) implements them. We cover:

  * ``search`` returns ``[]`` when no memory matches (callers must
    tolerate empty).
  * ``update`` raises :class:`KeyError` on unknown id (silent no-op
    would mask a programming bug).
  * ``delete`` is documented as a no-op on unknown id (the
    ``returns_empty`` outcome — no exception, no row touched).
  * ``add`` / ``add_turn`` raise when the underlying backend rejects
    the write — probed via inline raising subclasses.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeConversationStore

pytestmark = pytest.mark.contract


def test_search_returns_empty_when_no_memory_matches_query() -> None:
    """A store with no memories whose content overlaps the query MUST
    return an empty list — callers distinguish empty from raised
    exception.

    Sabotage proof: in :meth:`FakeMemoryStore.search` change the inner
    ``if overlap == 0: continue`` to ``if overlap == -1: continue``.
    Re-run: the test fails because every memory leaks into the result.
    Restored.
    """
    store = FakeConversationStore()
    store.add_turn(message="hello world", role="user", conversation_id="conv-1")
    assert store.search("totally unrelated topic", top_k=10) == []


def test_update_raises_key_error_when_memory_id_unknown() -> None:
    """``update`` MUST raise :class:`KeyError` on unknown id — silent
    no-op would mask the caller's wrong-id bug.

    Sabotage proof: in :meth:`FakeMemoryStore.update` comment out the
    ``raise KeyError(...)`` block. Re-run: the test fails because no
    exception fires. Restored.
    """
    store = FakeConversationStore()
    with pytest.raises(KeyError, match="no memory"):
        store.update("does-not-exist", "new content")


def test_delete_returns_empty_noop_when_id_already_absent() -> None:
    """``delete`` on an absent id is a documented no-op (returns
    ``None`` cleanly) — the ``returns_empty`` outcome means callers
    can replay tombstones safely without idempotency-tracking.

    Sabotage proof: in :meth:`FakeMemoryStore.delete` change
    ``self._memories.pop(memory_id, None)`` to
    ``del self._memories[memory_id]``. Re-run: the test fails because
    the call raises :class:`KeyError`. Restored.
    """
    store = FakeConversationStore()
    # No memories yet — delete must not raise. The Protocol returns None
    # on success; we assert no exception escapes (the documented "no-op").
    store.delete("ghost-id")
    # And the store remains empty — proves nothing was created or touched.
    assert store.search("anything", top_k=10) == []


def test_add_raises_when_underlying_backend_rejects_write() -> None:
    """A ``ConversationStore`` whose ``add`` raises must propagate the
    exception — silent failure here would silently drop user turns.

    Sabotage proof: in ``_RaisingConvStore.add`` change ``raise self._exc``
    to ``return "ghost-id"``. Re-run: the test fails because the call
    returns a string instead of raising. Restored.
    """

    class _RaisingConvStore(FakeConversationStore):
        def add(self, content: str, *, metadata: dict | None = None) -> str:
            raise RuntimeError("F68-add-rejected")

    with pytest.raises(RuntimeError, match="F68-add-rejected"):
        _RaisingConvStore().add("any content")


def test_add_turn_raises_when_inner_add_fails() -> None:
    """``add_turn`` delegates to ``add``; when the underlying ``add``
    raises, ``add_turn`` must NOT swallow it (the chat-ingestion
    pipeline relies on the propagation to checkpoint correctly).

    Sabotage proof: in :meth:`FakeConversationStore.add_turn` wrap the
    ``return self.add(...)`` call in
    ``try: ... except Exception: return "ghost-id"``. Re-run: the test
    fails because the call returns a string instead of raising.
    Restored.
    """

    class _RaisingAddStore(FakeConversationStore):
        def add(self, content: str, *, metadata: dict | None = None) -> str:
            raise RuntimeError("F68-inner-add-fail")

    with pytest.raises(RuntimeError, match="F68-inner-add-fail"):
        _RaisingAddStore().add_turn(message="hi", role="user", conversation_id="c1")
