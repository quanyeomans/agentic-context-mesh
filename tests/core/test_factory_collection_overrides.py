"""Unit tests for ``kairix.core.factory.derive_collection_overrides``
(canonical-collapse).

The per-collection retrieval override read is sourced from the canonical
topology collections (``topology.collections[*].retrieval``) via a
config-parse — db-free at pipeline-construction time.
``derive_collection_overrides`` parses the merged config mapping and
returns ``{collection_name: override_dict}`` for every collection that
declares a ``retrieval:`` block; collections without an override are
omitted so the resolver falls back to the global retrieval config.

Mirrors ``derive_tier_map`` exactly (overlay-aware, default-safe). Tests
inject the mapping through the ``mapping=`` seam (same shape as
``_resolve_retrieval_config(config=...)`` elsewhere in the factory) — no
``@patch`` (F1), no ``KAIRIX_*`` setenv (F2), no internal-name imports
beyond the public ``derive_collection_overrides`` (F5).
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.factory import derive_collection_overrides


def _topology_mapping(collections: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a merged-config mapping carrying a ``topology.collections``
    block — the canonical source ``derive_collection_overrides`` parses from.
    """
    return {"topology": {"collections": collections}}


@pytest.mark.unit
def test_derive_collection_overrides_returns_name_to_override() -> None:
    """A topology collection that declares a ``retrieval:`` block surfaces
    in the map keyed by its collection name, with the raw override dict."""
    mapping = _topology_mapping(
        [
            {
                "name": "reflib",
                "sources": [],
                "retrieval": {"fusion_strategy": "bm25_primary", "bm25_limit": 20},
            },
        ]
    )

    result = derive_collection_overrides(mapping=mapping)

    assert result == {"reflib": {"fusion_strategy": "bm25_primary", "bm25_limit": 20}}


@pytest.mark.unit
def test_derive_collection_overrides_returns_plain_dict_values() -> None:
    """Each override value is a plain mutable ``dict`` (not the frozen
    MappingProxyType the parser stores) so the consumer can splat it into
    ``merge_retrieval_config`` without surprises."""
    mapping = _topology_mapping(
        [
            {"name": "reflib", "sources": [], "retrieval": {"rrf_k": 10}},
        ]
    )

    result = derive_collection_overrides(mapping=mapping)

    assert type(result["reflib"]) is dict


@pytest.mark.unit
def test_derive_collection_overrides_omits_collection_without_override() -> None:
    """A collection with no ``retrieval:`` block is omitted (the resolver
    then uses the global retrieval config for that collection)."""
    mapping = _topology_mapping(
        [
            {"name": "reflib", "sources": [], "retrieval": {"rrf_k": 10}},
            {"name": "team-scratch", "sources": []},
        ]
    )

    result = derive_collection_overrides(mapping=mapping)

    assert result == {"reflib": {"rrf_k": 10}}
    assert "team-scratch" not in result


@pytest.mark.unit
def test_derive_collection_overrides_empty_when_no_topology_collections() -> None:
    """A mapping with no topology collections yields an empty map — the
    default-safe answer that falls back to the global retrieval config."""
    assert derive_collection_overrides(mapping={}) == {}


@pytest.mark.unit
def test_derive_collection_overrides_empty_when_no_collection_declares_override() -> None:
    """When every collection omits ``retrieval:``, the map is empty rather
    than a name→None dict."""
    mapping = _topology_mapping(
        [
            {"name": "reflib", "sources": []},
            {"name": "team-scratch", "sources": []},
        ]
    )

    assert derive_collection_overrides(mapping=mapping) == {}


@pytest.mark.unit
def test_derive_collection_overrides_maps_multiple_collections() -> None:
    """Several collections with overrides all surface, each keyed by its
    own name with its own override dict."""
    mapping = _topology_mapping(
        [
            {"name": "reflib", "sources": [], "retrieval": {"fusion_strategy": "bm25_primary"}},
            {"name": "team-canon", "sources": [], "retrieval": {"rrf_k": 5}},
            {"name": "team-scratch", "sources": []},
        ]
    )

    result = derive_collection_overrides(mapping=mapping)

    assert result == {
        "reflib": {"fusion_strategy": "bm25_primary"},
        "team-canon": {"rrf_k": 5},
    }


@pytest.mark.unit
def test_derive_collection_overrides_returns_empty_on_malformed_topology() -> None:
    """A structurally malformed topology block degrades to an empty map
    rather than raising at pipeline-construction time."""
    # ``collections`` must be a list; a string trips the parser's type
    # guard, which derive_collection_overrides swallows into the
    # default-safe {}.
    assert derive_collection_overrides(mapping={"topology": {"collections": "nope"}}) == {}
