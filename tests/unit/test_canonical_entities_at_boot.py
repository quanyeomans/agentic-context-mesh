"""Unit tests for #431 worker-startup canonical-entity seeding.

Tests the two seams:

* :func:`kairix.core.search.config_loader.load_canonical_entities` —
  reads the YAML block, returns a typed list of CanonicalEntity.
* :func:`kairix.worker.seed_canonical_entities_at_boot` — orchestrates
  the load + Neo4j seeding step the worker runs at boot.

Both are failure-isolated; the boot step never raises. F1/F2-clean:
all injection happens via the public kwarg seam (deps dataclasses).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from kairix.core.search.config_loader import load_canonical_entities
from kairix.knowledge.entities.canonical import CanonicalEntity
from kairix.worker import (
    CanonicalEntitySeedDeps,
    seed_canonical_entities_at_boot,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# load_canonical_entities
# ---------------------------------------------------------------------------


def test_load_canonical_entities_reads_yaml_block(tmp_path: Path) -> None:
    """The loader pulls the ``canonical_entities:`` block from
    kairix.config.yaml and returns a list of CanonicalEntity."""
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            provider: fake
            canonical_entities:
              - name: Shape
                type: agent
                summary: Strategic agent.
              - name: Acme Corp
                type: organisation
                summary: Software vendor.
                aliases: [Acme, Acme Inc.]
            """
        ).lstrip()
    )
    entities = load_canonical_entities(cfg)
    assert len(entities) == 2
    assert entities[0].name == "Shape"
    assert entities[0].entity_type == "agent"
    assert entities[1].aliases == ("Acme", "Acme Inc.")


def test_load_canonical_entities_returns_empty_when_block_absent(tmp_path: Path) -> None:
    """A config file with no ``canonical_entities:`` block returns an
    empty list — the loader doesn't manufacture canonicals out of
    nothing."""
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text("provider: fake\nretrieval: {}\n")
    assert load_canonical_entities(cfg) == []


def test_load_canonical_entities_returns_empty_when_path_missing(tmp_path: Path) -> None:
    """A missing config path returns empty — the boot step won't crash
    on a fresh install that hasn't placed a config yet."""
    missing = tmp_path / "does-not-exist.yaml"
    assert load_canonical_entities(missing) == []


def test_load_canonical_entities_returns_empty_on_malformed_yaml(tmp_path: Path) -> None:
    """Malformed YAML degrades to empty list with a logged warning —
    operators can fix the YAML without crashlooping the worker."""
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text("{{{{ invalid yaml :::::\n")
    assert load_canonical_entities(cfg) == []


# ---------------------------------------------------------------------------
# seed_canonical_entities_at_boot
# ---------------------------------------------------------------------------


class _FakeNeo4jForBoot:
    """Records upsert_node calls so the test can assert which canonicals
    reached Neo4j during the boot step."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.upserts: list[tuple[str, str, dict[str, Any]]] = []

    def upsert_node(self, label: str, node_id: str, props: dict[str, Any]) -> bool:
        self.upserts.append((label, node_id, props))
        return True


def test_seed_canonical_entities_at_boot_upserts_each_entry() -> None:
    """The boot step loads canonicals + upserts each into Neo4j.

    Sabotage-proof: drop the ``seed_canonical_entities(client, canonicals)``
    call inside the boot step and the count returned drops to 0 + the
    fake's ``upserts`` list stays empty.
    """
    fake = _FakeNeo4jForBoot()
    deps = CanonicalEntitySeedDeps(
        load_canonical_entities_fn=lambda: [
            CanonicalEntity(name="Shape", entity_type="agent", summary="Strategic agent"),
            CanonicalEntity(name="Acme Corp", entity_type="organisation", summary="Vendor"),
        ],
        neo4j_client_fn=lambda: fake,
    )
    seeded = seed_canonical_entities_at_boot(deps)
    assert seeded == 2
    assert len(fake.upserts) == 2


def test_seed_canonical_entities_at_boot_empty_when_no_canonicals_declared() -> None:
    """When the loader returns empty, the boot step is a no-op."""
    fake = _FakeNeo4jForBoot()
    deps = CanonicalEntitySeedDeps(
        load_canonical_entities_fn=lambda: [],
        neo4j_client_fn=lambda: fake,
    )
    assert seed_canonical_entities_at_boot(deps) == 0
    assert fake.upserts == []


def test_seed_canonical_entities_at_boot_isolates_loader_failure(caplog) -> None:
    """If the YAML loader raises, the boot step returns 0 and logs
    a WARN — never crashlooping the worker."""
    import logging

    def _raising_loader() -> list[Any]:
        raise RuntimeError("simulated config corruption")

    fake = _FakeNeo4jForBoot()
    deps = CanonicalEntitySeedDeps(
        load_canonical_entities_fn=_raising_loader,
        neo4j_client_fn=lambda: fake,
    )
    with caplog.at_level(logging.WARNING):
        seeded = seed_canonical_entities_at_boot(deps)
    assert seeded == 0
    assert fake.upserts == []
    assert any("could not load config" in r.getMessage() for r in caplog.records)


def test_seed_canonical_entities_at_boot_isolates_neo4j_unavailable() -> None:
    """Neo4j unavailable → seeded=0; the boot step doesn't crash."""
    fake = _FakeNeo4jForBoot(available=False)
    deps = CanonicalEntitySeedDeps(
        load_canonical_entities_fn=lambda: [
            CanonicalEntity(name="Shape", entity_type="agent", summary="x"),
        ],
        neo4j_client_fn=lambda: fake,
    )
    assert seed_canonical_entities_at_boot(deps) == 0


def test_seed_canonical_entities_at_boot_isolates_neo4j_client_construct_failure(
    caplog,
) -> None:
    """If neo4j_client_fn raises, the boot step returns 0 + logs WARN."""
    import logging

    def _raising_client() -> Any:
        raise RuntimeError("neo4j down")

    deps = CanonicalEntitySeedDeps(
        load_canonical_entities_fn=lambda: [
            CanonicalEntity(name="Shape", entity_type="agent", summary="x"),
        ],
        neo4j_client_fn=_raising_client,
    )
    with caplog.at_level(logging.WARNING):
        seeded = seed_canonical_entities_at_boot(deps)
    assert seeded == 0
    assert any("seed failed" in r.getMessage() for r in caplog.records)
