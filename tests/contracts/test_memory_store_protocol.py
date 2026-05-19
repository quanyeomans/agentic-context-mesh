"""Contract: MemoryStore + ConversationStore Protocol compliance.

Phase 0.1 of the mem0-vs-kairix-uplift plan. Pins the surface that
``KairixNativeMemoryStore`` and ``Mem0MemoryStore`` both implement so
upcoming backend swaps cannot silently regress the boundary.

Two layers of test:

1. **Protocol compliance** — ``isinstance(fake, MemoryStore)`` returns
   True. Sabotage-proof: delete one method from ``FakeMemoryStore``
   and the isinstance check fails. Verified locally; transcript in the
   commit body.
2. **Round-trip semantics** — add → search → update → search → delete
   → search proves the documented contract through the public surface.
   No internal-attribute access, no monkeypatching.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import ConversationStore, Memory, MemoryStore
from tests.fakes import FakeConversationStore, FakeMemoryStore

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Protocol compliance — sabotage-proven via isinstance() at runtime
# ---------------------------------------------------------------------------


def test_fake_memory_store_satisfies_memory_store_protocol() -> None:
    """FakeMemoryStore satisfies MemoryStore via runtime isinstance().

    Sabotage-proof: remove the ``delete`` method from FakeMemoryStore
    in tests/fakes.py and this assertion fails because runtime_checkable
    isinstance probes for all four methods.
    """
    assert isinstance(FakeMemoryStore(), MemoryStore)


def test_fake_conversation_store_satisfies_both_protocols() -> None:
    """FakeConversationStore satisfies BOTH ConversationStore AND MemoryStore.

    Sabotage-proof: remove ``add_turn`` and only the ConversationStore
    assertion fails; remove ``add`` and both fail.
    """
    store = FakeConversationStore()
    assert isinstance(store, MemoryStore), "ConversationStore must include the MemoryStore surface"
    assert isinstance(store, ConversationStore), "ConversationStore must include add_turn"


def test_search_returns_memory_protocol_objects() -> None:
    """``MemoryStore.search`` returns objects satisfying the Memory Protocol.

    Sabotage-proof: change ``FakeMemory.score`` from a property to a
    regular attribute named ``_score``; isinstance(result, Memory) fails
    because the Protocol's runtime probe looks for an attribute named
    ``score``.
    """
    store = FakeMemoryStore()
    store.add("alpha beta gamma", metadata={"source": "test"})
    results = store.search("alpha", top_k=5)
    assert results, "search must surface a memory whose content shares words with the query"
    for m in results:
        assert isinstance(m, Memory), (
            f"every search result must satisfy Memory Protocol; got {type(m).__name__}. "
            f"fix: add id/content/score/metadata properties to the result type"
        )


# ---------------------------------------------------------------------------
# Round-trip semantics — the documented contract
# ---------------------------------------------------------------------------


def test_add_returns_id_and_round_trips_through_search() -> None:
    """``add`` returns a non-empty id; subsequent ``search`` surfaces it."""
    store = FakeMemoryStore()
    mem_id = store.add("the quick brown fox", metadata={"source": "test"})
    assert isinstance(mem_id, str) and mem_id, "add must return a non-empty string id"
    results = store.search("quick", top_k=5)
    matched = [m for m in results if m.id == mem_id]
    assert matched, f"search('quick') must surface id={mem_id!r}; got ids={[m.id for m in results]}"
    assert "the quick brown fox" in matched[0].content


def test_update_replaces_content_and_search_reflects_change() -> None:
    """``update`` replaces content; the new content is what ``search`` returns."""
    store = FakeMemoryStore()
    mem_id = store.add("original content")
    store.update(mem_id, "completely different replacement")
    matched = [m for m in store.search("replacement", top_k=5) if m.id == mem_id]
    assert matched, "search must surface the memory under the new content's terms"
    assert matched[0].content == "completely different replacement"
    # And the old content is no longer findable for this id
    old_match = [m for m in store.search("original", top_k=5) if m.id == mem_id]
    assert not old_match, "old content must not be searchable after update"


def test_update_missing_id_raises_key_error() -> None:
    """``update`` on a non-existent id raises KeyError per the Protocol docstring."""
    store = FakeMemoryStore()
    with pytest.raises(KeyError, match="no memory with id"):
        store.update("not-a-real-id", "new content")


def test_delete_removes_memory_and_search_no_longer_surfaces_it() -> None:
    """``delete`` removes the memory; subsequent ``search`` excludes it."""
    store = FakeMemoryStore()
    mem_id = store.add("ephemeral content here")
    store.delete(mem_id)
    results = store.search("ephemeral", top_k=5)
    assert not [m for m in results if m.id == mem_id], "deleted memory must not surface in search"


def test_delete_missing_id_is_noop() -> None:
    """``delete`` of a non-existent id is a no-op per the Protocol docstring."""
    store = FakeMemoryStore()
    # Must not raise
    store.delete("not-a-real-id")
    # And other state is untouched
    mem_id = store.add("still here")
    assert store.search("still", top_k=5), "delete-noop must not corrupt other state"
    assert mem_id


def test_search_empty_store_returns_empty_list() -> None:
    """``search`` on an empty store returns ``[]``, not None, not raises."""
    store = FakeMemoryStore()
    result = store.search("anything", top_k=10)
    assert result == [], f"empty store must return []; got {result!r}"


def test_search_top_k_caps_result_size() -> None:
    """``search`` returns at most ``top_k`` memories."""
    store = FakeMemoryStore()
    for i in range(20):
        store.add(f"common term entry number {i}")
    result = store.search("common", top_k=3)
    assert len(result) == 3, f"top_k=3 must return at most 3 memories; got {len(result)}"


def test_search_returns_best_first_by_score() -> None:
    """``search`` returns memories sorted by score, best first."""
    store = FakeMemoryStore()
    store.add("alpha beta gamma delta epsilon")  # 5 words; query "alpha beta" → 2/2 overlap
    store.add("alpha zeta eta theta iota kappa")  # 6 words; query "alpha beta" → 1/2 overlap
    result = store.search("alpha beta", top_k=2)
    assert len(result) == 2
    assert result[0].score >= result[1].score, (
        f"results must be sorted by score descending; got {result[0].score} then {result[1].score}"
    )


# ---------------------------------------------------------------------------
# ConversationStore — turn-level ingestion round-trips into the search surface
# ---------------------------------------------------------------------------


def test_add_turn_metadata_round_trips_through_search() -> None:
    """``add_turn``'s role/conversation_id/timestamp survive into ``search`` metadata.

    Sabotage-proof: change ``add_turn`` to drop ``conversation_id`` from
    the metadata dict; this assertion fails because the round-trip loses
    the field.
    """
    store = FakeConversationStore()
    mem_id = store.add_turn(
        message="The benchmark expects this exact phrase",
        role="user",
        conversation_id="conv-26",
        timestamp="2026-05-20T07:00:00Z",
    )
    results = store.search("benchmark", top_k=5)
    matched = [m for m in results if m.id == mem_id]
    assert matched, "search must find the just-added turn"
    md = matched[0].metadata
    assert md["role"] == "user", f"role lost in round-trip; got {md!r}"
    assert md["conversation_id"] == "conv-26", f"conversation_id lost in round-trip; got {md!r}"
    assert md["timestamp"] == "2026-05-20T07:00:00Z", f"timestamp lost in round-trip; got {md!r}"


def test_add_turn_assigns_default_timestamp_when_omitted() -> None:
    """``add_turn`` without ``timestamp`` stamps a default rather than ``None``.

    Backends with proper now-stamping behaviour will produce a real ISO-8601
    string; the FakeConversationStore uses an epoch sentinel so the test is
    deterministic. Either way, metadata['timestamp'] must be a string.
    """
    store = FakeConversationStore()
    mem_id = store.add_turn(message="no timestamp", role="user", conversation_id="conv-x")
    matched = [m for m in store.search("timestamp", top_k=5) if m.id == mem_id]
    assert matched
    assert isinstance(matched[0].metadata["timestamp"], str)
    assert matched[0].metadata["timestamp"], "timestamp must be a non-empty string"
