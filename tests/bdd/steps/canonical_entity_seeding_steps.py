"""Step impls for canonical_entity_seeding.feature (#431 deferred BDD).

Drives the public :func:`parse_canonical_entities` + :func:`seed_canonical_entities`
surfaces through a Protocol-compliant Neo4j fake. F1/F2-clean — no
monkey-patching, no env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.knowledge.entities.canonical import (
    parse_canonical_entities,
    seed_canonical_entities,
)

pytestmark = pytest.mark.bdd


class _FakeNeo4jClient:
    """Minimal Neo4jClient surface for the BDD: records upsert_node calls."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.upserts: list[tuple[str, str, dict[str, Any]]] = []

    def upsert_node(self, label: str, node_id: str, props: dict[str, Any]) -> bool:
        self.upserts.append((label, node_id, props))
        return True


@dataclass
class _Ctx:
    declarations: list[dict[str, Any]] = field(default_factory=list)
    neo4j_available: bool = True
    seeded_count: int = 0
    client: _FakeNeo4jClient | None = None


@pytest.fixture
def canon_ctx() -> _Ctx:
    return _Ctx()


@given(parsers.parse("the operator has declared entity '{name}' of type '{etype}' with summary '{summary}'"))
def _declare_with_summary(canon_ctx: _Ctx, name: str, etype: str, summary: str) -> None:
    canon_ctx.declarations.append({"name": name, "type": etype, "summary": summary})


@given(parsers.parse("the operator has declared entity '{name}' of type '{etype}' with aliases '{aliases}'"))
def _declare_with_aliases(canon_ctx: _Ctx, name: str, etype: str, aliases: str) -> None:
    canon_ctx.declarations.append(
        {
            "name": name,
            "type": etype,
            "summary": "",
            "aliases": [a.strip() for a in aliases.split(",")],
        }
    )


@given("Neo4j is unavailable for canonical seeding")
def _unavailable(canon_ctx: _Ctx) -> None:
    canon_ctx.neo4j_available = False


@when("the worker startup seeds canonical entities into Neo4j")
def _seed(canon_ctx: _Ctx) -> None:
    parsed = parse_canonical_entities(canon_ctx.declarations)
    canon_ctx.client = _FakeNeo4jClient(available=canon_ctx.neo4j_available)
    canon_ctx.seeded_count = seed_canonical_entities(canon_ctx.client, parsed)


@then(parsers.parse("Neo4j receives an upsert for '{name}' under the '{label}' label"))
def _then_upsert_landed(canon_ctx: _Ctx, name: str, label: str) -> None:
    assert canon_ctx.client is not None
    matches = [u for u in canon_ctx.client.upserts if u[0] == label and u[2].get("name") == name]
    assert matches, f"expected an upsert for {name!r} under label {label!r}; got {canon_ctx.client.upserts!r}"


@then("the upsert carries kairix_canonical=true")
def _then_canonical_flag(canon_ctx: _Ctx) -> None:
    assert canon_ctx.client is not None
    assert canon_ctx.client.upserts, "no upserts recorded"
    _, _, props = canon_ctx.client.upserts[0]
    assert props.get("kairix_canonical") is True, f"expected kairix_canonical=true on props; got {props!r}"


@then("the upsert props include the aliases list")
def _then_aliases(canon_ctx: _Ctx) -> None:
    assert canon_ctx.client is not None
    assert canon_ctx.client.upserts, "no upserts recorded"
    _, _, props = canon_ctx.client.upserts[0]
    aliases = props.get("aliases")
    assert aliases, f"expected aliases on props; got {props!r}"
    assert "Acme" in aliases
    assert "Acme Inc." in aliases


@then("zero entities are seeded")
def _then_zero_seeded(canon_ctx: _Ctx) -> None:
    assert canon_ctx.seeded_count == 0


@then("no upserts land")
def _then_no_upserts(canon_ctx: _Ctx) -> None:
    assert canon_ctx.client is not None
    assert canon_ctx.client.upserts == []
