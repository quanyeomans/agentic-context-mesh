"""Unit tests for ``kairix.core.factory.derive_tier_map`` (Task 6).

The ranking-tier read is sourced from the canonical topology collections
(``topology_v2.collections[*].tier``) via a config-parse — db-free at
pipeline-construction time. ``derive_tier_map`` parses the merged config
mapping and returns ``{collection_name: tier}`` for every collection that
declares a ``tier:``; collections without a tier are omitted so the
:class:`SourceTierBoost` falls back to its default (x1.0) multiplier.

Tests inject the mapping through the ``mapping=`` seam (same shape as
``_resolve_retrieval_config(config=...)`` elsewhere in the factory) — no
``@patch`` (F1), no ``KAIRIX_*`` setenv (F2), no internal-name imports
beyond the public ``derive_tier_map`` (F5).
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.factory import derive_tier_map


def _topology_mapping(collections: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a merged-config mapping carrying a ``topology_v2.collections``
    block — the canonical source ``derive_tier_map`` parses from.
    """
    return {"topology_v2": {"collections": collections}}


@pytest.mark.unit
def test_derive_tier_map_returns_name_to_tier_for_tiered_collection() -> None:
    """A topology collection that declares ``tier:`` surfaces in the map
    keyed by its collection name."""
    mapping = _topology_mapping(
        [
            {"name": "team-canon", "sources": [], "tier": "reference"},
        ]
    )

    result = derive_tier_map(mapping=mapping)

    assert result == {"team-canon": "reference"}


@pytest.mark.unit
def test_derive_tier_map_omits_collection_without_tier() -> None:
    """A collection with no ``tier:`` is omitted (SourceTierBoost then
    falls back to its default x1.0 multiplier for that collection)."""
    mapping = _topology_mapping(
        [
            {"name": "team-canon", "sources": [], "tier": "reference"},
            {"name": "team-scratch", "sources": []},
        ]
    )

    result = derive_tier_map(mapping=mapping)

    assert result == {"team-canon": "reference"}
    assert "team-scratch" not in result


@pytest.mark.unit
def test_derive_tier_map_empty_when_no_topology_collections() -> None:
    """A mapping with no topology collections yields an empty map — the
    default-safe answer that preserves pre-#432 ranking byte-for-byte."""
    assert derive_tier_map(mapping={}) == {}


@pytest.mark.unit
def test_derive_tier_map_empty_when_no_collection_declares_tier() -> None:
    """When every collection omits ``tier:``, the map is empty rather than
    a name→None dict."""
    mapping = _topology_mapping(
        [
            {"name": "team-canon", "sources": []},
            {"name": "team-scratch", "sources": []},
        ]
    )

    assert derive_tier_map(mapping=mapping) == {}


@pytest.mark.unit
def test_derive_tier_map_maps_multiple_tiered_collections() -> None:
    """Several tiered collections all surface, each keyed by its own name
    with its own tier value."""
    mapping = _topology_mapping(
        [
            {"name": "team-canon", "sources": [], "tier": "canonical"},
            {"name": "team-archive", "sources": [], "tier": "archived"},
            {"name": "team-scratch", "sources": []},
        ]
    )

    result = derive_tier_map(mapping=mapping)

    assert result == {"team-canon": "canonical", "team-archive": "archived"}


@pytest.mark.unit
def test_derive_tier_map_returns_empty_on_malformed_topology() -> None:
    """A structurally malformed topology block degrades to an empty map
    rather than raising at pipeline-construction time."""
    # ``collections`` must be a list; a string trips the parser's type
    # guard, which derive_tier_map swallows into the default-safe {}.
    assert derive_tier_map(mapping={"topology_v2": {"collections": "nope"}}) == {}
