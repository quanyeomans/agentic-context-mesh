"""Tests for kairix.knowledge.entities.canonical (Issue #431, EPIC #438)."""

import logging
from typing import Any

import pytest

from kairix.knowledge.entities.canonical import (
    FIRST_PARTY_CANONICAL_ENTITIES,
    CanonicalEntity,
    merge_canonical_entities,
    parse_canonical_entities,
    seed_canonical_entities,
)

# ---------------------------------------------------------------------------
# Fake Neo4j client — records upsert calls for assertion
# ---------------------------------------------------------------------------


class _FakeNeo4jClient:
    """Minimal Neo4jClient surface for seed_canonical_entities testing.

    Records every upsert_node call. ``available`` defaults to True;
    tests set it False to drive the unavailable-graph branch.
    """

    def __init__(self, *, available: bool = True, raise_on_upsert: Exception | None = None) -> None:
        self.available = available
        self.upserts: list[tuple[str, str, dict[str, Any]]] = []
        self._raise = raise_on_upsert

    def upsert_node(self, label: str, node_id: str, props: dict[str, Any]) -> bool:
        if self._raise is not None:
            raise self._raise
        self.upserts.append((label, node_id, props))
        return True


# ---------------------------------------------------------------------------
# parse_canonical_entities
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_first_party_seed_declares_kairix() -> None:
    """The built-in first-party seed (#467) always includes ``Kairix`` with a
    non-empty summary, so ``facts_about('Kairix')`` resolves out of the box.

    Sabotage-proof: remove the ``Kairix`` entry from
    ``FIRST_PARTY_CANONICAL_ENTITIES`` → this lookup returns nothing and the
    assertions below fail.
    """
    by_name = {e.name: e for e in FIRST_PARTY_CANONICAL_ENTITIES}
    assert "Kairix" in by_name
    assert by_name["Kairix"].summary.strip() != ""
    assert "kairix" in by_name["Kairix"].aliases


@pytest.mark.unit
def test_merge_appends_first_party_floor_under_operator_declarations() -> None:
    """Operator entries come first (declared order); un-shadowed built-ins
    (#467) are appended, so ``Kairix`` is present alongside operator canon."""
    operator = [CanonicalEntity(name="Acme Corp", entity_type="organisation", summary="Vendor.")]
    merged = merge_canonical_entities(operator)
    names = [e.name for e in merged]
    assert names[0] == "Acme Corp"
    assert "Kairix" in names
    assert "Three Cubes" in names


@pytest.mark.unit
def test_merge_lets_operator_override_a_built_in_by_name() -> None:
    """An operator who declares their own ``Kairix`` (case-insensitive) fully
    overrides the built-in — the seed is a floor, never a ceiling (#467).

    Sabotage-proof: drop the ``declared_names`` filter in
    ``merge_canonical_entities`` → both the operator's Kairix and the built-in
    Kairix appear, so the single-entry assertion below fails.
    """
    operator = [CanonicalEntity(name="kairix", entity_type="platform_component", summary="Operator override.")]
    merged = merge_canonical_entities(operator)
    kairix_entries = [e for e in merged if e.name.lower() == "kairix"]
    assert len(kairix_entries) == 1
    assert kairix_entries[0].summary == "Operator override."


@pytest.mark.unit
def test_merge_of_empty_operator_list_is_exactly_the_floor() -> None:
    """No operator declarations → the merged list is exactly the built-in
    floor, preserving its order."""
    assert merge_canonical_entities([]) == list(FIRST_PARTY_CANONICAL_ENTITIES)


@pytest.mark.unit
def test_parse_canonical_entities_returns_empty_list_for_none():
    """Missing canonical_entities block (None) → []. Preserves pre-#431
    behaviour for operators who haven't declared any canonicals."""
    assert parse_canonical_entities(None) == []


@pytest.mark.unit
def test_parse_canonical_entities_returns_empty_list_for_empty_list():
    """Empty list (explicitly declared but no entries) → []."""
    assert parse_canonical_entities([]) == []


@pytest.mark.unit
def test_parse_canonical_entities_returns_empty_list_for_non_list(caplog):
    """A top-level value that isn't a list logs a warning + returns []."""
    with caplog.at_level(logging.WARNING):
        assert parse_canonical_entities({"name": "Shape"}) == []
    assert any("not a list" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_parse_canonical_entities_parses_full_entry():
    """A complete YAML entry produces a CanonicalEntity with every field."""
    raw = [
        {
            "name": "Shape",
            "type": "agent",
            "summary": "Strategic + design-orchestration agent.",
            "aliases": ["shape-agent", "Shape Agent"],
        }
    ]
    parsed = parse_canonical_entities(raw)
    assert len(parsed) == 1
    e = parsed[0]
    assert e.name == "Shape"
    assert e.entity_type == "agent"
    assert e.summary == "Strategic + design-orchestration agent."
    assert e.aliases == ("shape-agent", "Shape Agent")


@pytest.mark.unit
def test_parse_canonical_entities_handles_missing_optional_fields():
    """When ``summary`` and ``aliases`` are absent, defaults kick in."""
    raw = [{"name": "Kairix", "type": "platform_component"}]
    parsed = parse_canonical_entities(raw)
    assert len(parsed) == 1
    assert parsed[0].summary == ""
    assert parsed[0].aliases == ()


@pytest.mark.unit
def test_parse_canonical_entities_skips_entries_missing_name_or_type(caplog):
    """A malformed entry (missing required field) is skipped with a
    warning — does NOT raise. One typo shouldn't break startup."""
    raw = [
        {"name": "Shape", "type": "agent"},
        {"summary": "no name or type"},  # malformed
        {"name": "OpenClaw"},  # missing type
        {"type": "agent"},  # missing name
        {"name": "Kairix", "type": "platform_component"},
    ]
    with caplog.at_level(logging.WARNING):
        parsed = parse_canonical_entities(raw)
    assert [e.name for e in parsed] == ["Shape", "Kairix"]
    # At least 3 warnings (one per malformed entry)
    warning_count = sum(1 for r in caplog.records if "missing required" in r.getMessage())
    assert warning_count >= 3


@pytest.mark.unit
def test_parse_canonical_entities_preserves_input_order():
    """Canonicals come out in operator-declared order — callers that
    care about deterministic seed ordering can rely on this."""
    raw = [
        {"name": "Zulu", "type": "agent"},
        {"name": "Alpha", "type": "agent"},
        {"name": "Mike", "type": "agent"},
    ]
    parsed = parse_canonical_entities(raw)
    assert [e.name for e in parsed] == ["Zulu", "Alpha", "Mike"]


@pytest.mark.unit
def test_parse_canonical_entities_handles_non_list_aliases_safely(caplog):
    """If 'aliases' is a string instead of a list, default to () with a
    warning. Common operator typo."""
    raw = [{"name": "Shape", "type": "agent", "aliases": "shape-agent"}]
    with caplog.at_level(logging.WARNING):
        parsed = parse_canonical_entities(raw)
    assert parsed[0].aliases == ()


# ---------------------------------------------------------------------------
# seed_canonical_entities
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_seed_canonical_entities_upserts_each_entity():
    """Every canonical entity drives one upsert_node call with the
    expected (label, slug, props) shape."""
    client = _FakeNeo4jClient()
    canonicals = [
        CanonicalEntity(name="Shape", entity_type="agent", summary="Strategic agent"),
        CanonicalEntity(name="OpenClaw", entity_type="platform_component", summary="Gateway"),
    ]
    upserted = seed_canonical_entities(client, canonicals)

    assert upserted == 2
    assert len(client.upserts) == 2
    # First upsert
    label, node_id, props = client.upserts[0]
    assert label == "agent"
    assert node_id == "shape"  # slug
    assert props["name"] == "Shape"
    assert props["summary"] == "Strategic agent"
    assert props["kairix_canonical"] is True


@pytest.mark.unit
def test_seed_canonical_entities_includes_aliases_in_props():
    """When aliases are declared, they appear as a list in the props."""
    client = _FakeNeo4jClient()
    canonicals = [
        CanonicalEntity(
            name="Shape",
            entity_type="agent",
            summary="Strategic agent",
            aliases=("shape-agent", "Shape Agent"),
        )
    ]
    seed_canonical_entities(client, canonicals)

    _, _, props = client.upserts[0]
    assert props["aliases"] == ["shape-agent", "Shape Agent"]


@pytest.mark.unit
def test_seed_canonical_entities_returns_zero_when_neo4j_unavailable(caplog):
    """Degraded Neo4j → 0 seeded, warning logged. Operator can re-run
    after recovery (idempotency holds)."""
    client = _FakeNeo4jClient(available=False)
    canonicals = [CanonicalEntity(name="Shape", entity_type="agent", summary="x")]
    with caplog.at_level(logging.WARNING):
        upserted = seed_canonical_entities(client, canonicals)
    assert upserted == 0
    assert client.upserts == []
    assert any("not available" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_seed_canonical_entities_is_idempotent():
    """Re-running with the same input replays the upserts (Neo4j MERGE
    semantics make this safe in production). The function returns the
    same count each call."""
    client = _FakeNeo4jClient()
    canonicals = [CanonicalEntity(name="Shape", entity_type="agent", summary="x")]
    first = seed_canonical_entities(client, canonicals)
    second = seed_canonical_entities(client, canonicals)
    assert first == 1
    assert second == 1
    assert len(client.upserts) == 2  # both calls recorded


@pytest.mark.unit
def test_seed_canonical_entities_swallows_per_entity_exceptions(caplog):
    """A single upsert failure doesn't abort the whole seed pass; the
    remaining entities still get upserted. Locks the contract that
    canonical seeding never breaks startup."""
    client = _FakeNeo4jClient(raise_on_upsert=RuntimeError("simulated"))
    canonicals = [CanonicalEntity(name="Shape", entity_type="agent", summary="x")]
    with caplog.at_level(logging.WARNING):
        upserted = seed_canonical_entities(client, canonicals)
    assert upserted == 0
    assert any("upsert failed" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_seed_canonical_entities_returns_zero_for_empty_canonicals_list():
    """No canonicals declared → no upserts, returns 0. Doesn't even log
    a warning (this is the operator's intentional empty state)."""
    client = _FakeNeo4jClient()
    upserted = seed_canonical_entities(client, [])
    assert upserted == 0
    assert client.upserts == []


@pytest.mark.unit
def test_seed_canonical_entities_slugifies_names_with_spaces_and_hyphens():
    """Canonical names with spaces / hyphens slugify to lowercase
    underscore-separated ids. Locks the convention so a downstream
    MATCH (n {id: 'foo_bar'}) finds the seeded node."""
    client = _FakeNeo4jClient()
    canonicals = [
        CanonicalEntity(name="Three Cubes", entity_type="organisation", summary="Ops shop."),
        CanonicalEntity(name="agent-zone", entity_type="platform_component", summary="Repo."),
    ]
    seed_canonical_entities(client, canonicals)
    slugs = [node_id for _, node_id, _ in client.upserts]
    assert slugs == ["three_cubes", "agent_zone"]
