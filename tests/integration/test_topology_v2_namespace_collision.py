"""Integration coverage for the #305 ``topology_v2:`` parent-key namespace.

Wave D originally landed six top-level YAML blocks
(``connectors:`` / ``credentials:`` / ``cc_pairs:`` / ``collections:`` /
``scope_profiles:`` / ``skills:``). The Wave D ``collections:`` block
collided with the legacy top-level ``collections.shared`` dict shape,
which forced ``kairix.core.search.config_validator._validate_topology_v2``
to skip the ``collections:`` block so legacy operators wouldn't break.
The skip in turn disabled cross-reference rule 3
(``collections.*.sources.*.cc_pair`` references a declared cc_pair).

#305 nests the six blocks under a single ``topology_v2:`` parent key.
This file pins three properties of that namespace fix:

1. The validator now wires rule 3 end-to-end — a malformed YAML where a
   ``topology_v2.collections.*.sources.*.cc_pair`` references a
   non-existent cc_pair surfaces a ``collection_source_cc_pair_missing``
   failure (acceptance criterion #3).
2. The validator now wires every other cross-reference rule
   (1, 2, 4, 5) end-to-end via the ``topology_v2:`` parent.
3. A legacy config with the top-level ``collections.shared`` dict shape
   AND no ``topology_v2:`` block still parses + validates exactly the
   same way it did before #305 — zero regression on legacy operators
   (acceptance criterion #4).

Sabotage proofs documented in the commit body (#305).
"""

from __future__ import annotations

import pytest

from kairix.core.search.config_validator import validate_config

pytestmark = pytest.mark.integration


def test_rule3_fires_for_dangling_collection_source_cc_pair() -> None:
    """A ``topology_v2.collections.*.sources.*.cc_pair`` reference that does not
    match any declared ``topology_v2.cc_pairs.*.id`` surfaces rule 3.

    Acceptance criterion #3 of #305 — with the parent-key namespace the
    ``collections:`` block no longer collides with the legacy
    ``collections.shared`` shape, so the validator stops skipping it and
    rule 3 fires end-to-end.

    Sabotage-proof (see commit body): mutating
    ``_validate_topology_v2`` to skip ``data.get("topology_v2")`` again
    causes this test to fail with an empty error list. Restored.
    """
    yaml_dict = {
        "topology_v2": {
            "connectors": [
                {"id": "obsidian-personal", "kind": "obsidian", "name": "obsidian-personal"},
            ],
            "cc_pairs": [
                {
                    "id": "obsidian-personal-default",
                    "connector": "obsidian-personal",
                    "credential": None,
                    "name": "obsidian-personal-default",
                },
            ],
            "collections": [
                {
                    "name": "vault-projects",
                    "sources": [
                        {"cc_pair": "never-declared-pair", "path_filter": "01-Projects/*"},
                    ],
                },
            ],
        }
    }
    errors = validate_config(yaml_dict)
    assert errors, "expected rule 3 to surface a failure; got empty error list"
    rule3_errors = [e for e in errors if "collection_source_cc_pair_missing" in e]
    assert rule3_errors, f"expected collection_source_cc_pair_missing rule; got: {errors!r}"
    rule3_msg = rule3_errors[0]
    assert "never-declared-pair" in rule3_msg
    assert "fix:" in rule3_msg
    assert "next: run" in rule3_msg


def test_validator_wires_remaining_rules_through_topology_v2_parent() -> None:
    """The other cross-reference rules (1 / 2 / 4 / 5) also fire end-to-end
    through the namespaced ``topology_v2:`` parent — the validator no
    longer skips any block.
    """
    yaml_dict = {
        "topology_v2": {
            "cc_pairs": [
                {
                    "id": "p1",
                    "connector": "ghost-connector",
                    "credential": "ghost-credential",
                    "name": "p1",
                },
            ],
            "scope_profiles": [
                {
                    "name": "agent-alpha",
                    "entries": [
                        {"actor_id": "agent-alpha", "collection_name": "ghost-collection"},
                    ],
                },
            ],
            "skills": [
                {
                    "name": "prepare",
                    "task_collections": [
                        {"name": "task", "sources": [{"cc_pair": "ghost-skill-pair"}]},
                    ],
                },
            ],
        }
    }
    errors = validate_config(yaml_dict)
    assert any("cc_pair_connector_missing" in e for e in errors), errors
    assert any("cc_pair_credential_missing" in e for e in errors), errors
    assert any("scope_profile_entry_collection_missing" in e for e in errors), errors
    assert any("skill_source_cc_pair_missing" in e for e in errors), errors


def test_legacy_collections_shared_dict_shape_still_validates() -> None:
    """Backward-compat: a legacy operator config with the top-level
    ``collections.shared`` dict shape AND no ``topology_v2:`` parent key
    still parses + validates byte-identically to its pre-#305 behaviour.

    Acceptance criterion #4 of #305 — the namespace fix is purely
    additive at the top level: it adds ``topology_v2:`` without removing
    or renaming the legacy ``collections.shared`` block. Operators on
    the legacy schema see zero behaviour change.

    Sabotage-proof (see commit body): the legacy schema validator was
    unchanged by #305; mutating
    ``_validate_topology_v2`` to always raise turns this test red
    because the top-level legacy ``collections.shared`` path still runs
    the topology v2 validator (with an absent parent key → no-op). The
    test pins that "absent parent key" → "no topology errors" wiring.
    """
    legacy_yaml_dict = {
        "collections": {
            "shared": [
                {"name": "vault-personal", "path": "/data/vault/personal"},
                {"name": "vault-shared", "path": "/data/vault/shared"},
            ],
            "agent_pattern": "{agent}-memory",
        },
        "agents": [
            {"name": "agent-alpha", "write_path": "/data/vault/personal/agent-alpha"},
        ],
    }
    errors = validate_config(legacy_yaml_dict)
    assert errors == [], f"legacy config should validate cleanly; got: {errors!r}"


def test_legacy_collections_shared_with_duplicate_name_still_reports_error() -> None:
    """Backward-compat sabotage: the legacy validator surface (which is
    independent of the topology v2 surface) keeps surfacing its
    pre-existing duplicate-name error against the legacy shape.

    Pins that the legacy ``collections.shared`` path is unchanged by
    #305 — sabotage-proof against accidentally short-circuiting the
    legacy validator while moving the topology v2 surface.
    """
    legacy_yaml_dict = {
        "collections": {
            "shared": [
                {"name": "vault", "path": "/data/vault/a"},
                {"name": "vault", "path": "/data/vault/b"},
            ],
        },
    }
    errors = validate_config(legacy_yaml_dict)
    assert any("duplicate collection name" in e for e in errors), errors


def test_legacy_config_with_topology_v2_block_runs_both_validators() -> None:
    """A hybrid config that carries BOTH the legacy ``collections.shared``
    dict AND a ``topology_v2:`` parent (operator partway through the
    migration) runs both validator surfaces independently.
    """
    hybrid_yaml_dict = {
        "collections": {
            "shared": [{"name": "vault", "path": "/data/vault"}],
        },
        "topology_v2": {
            "cc_pairs": [
                {"id": "p1", "connector": "missing-connector", "credential": None, "name": "p1"},
            ],
        },
    }
    errors = validate_config(hybrid_yaml_dict)
    # Legacy block validates clean — no errors for collections.shared
    assert not any("duplicate collection name" in e for e in errors), errors
    # Wave D rule 1 fires for the dangling connector ref
    assert any("cc_pair_connector_missing" in e for e in errors), errors
