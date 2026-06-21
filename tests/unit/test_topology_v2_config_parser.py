"""Unit tests for the Wave D topology v2 YAML parser.

Covers :func:`kairix.config.parse_topology_v2` — every block, every
edge (empty, missing required field, wrong list/dict shape, unknown
enum value). Sabotage-prove: see commit body for the 5 mutate→fail→
restore proofs per validator (this file primarily covers parse shape;
the validator file ships its own sabotage proofs).

Post #305: every payload nests the six Wave D blocks under a single
``topology_v2:`` parent key so the ``collections:`` block stops
colliding with the legacy top-level ``collections.shared`` dict shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.config import (
    CCPairConfig,
    CollectionConfig,
    CollectionSourceConfig,
    ConnectorConfig,
    CredentialConfig,
    ScopeEntryConfig,
    ScopeProfileConfig,
    SkillConfig,
    SkillSourceConfig,
    SkillTaskCollectionConfig,
    TopologyV2Config,
    parse_topology_v2,
)
from kairix.config.topology_v2 import TopologyV2ParseError

pytestmark = pytest.mark.unit


def test_empty_dict_parses_to_all_empty_config() -> None:
    """Backward-compat: a legacy YAML without a topology_v2 block parses cleanly."""
    config = parse_topology_v2({})
    assert config == TopologyV2Config()


def test_explicit_null_topology_v2_parses_to_all_empty_config() -> None:
    """``topology_v2: null`` (operator declaring the key but leaving it empty) parses cleanly."""
    config = parse_topology_v2({"topology_v2": None})
    assert config == TopologyV2Config()


def test_empty_topology_v2_mapping_parses_to_all_empty_config() -> None:
    """``topology_v2: {}`` (empty mapping) parses cleanly."""
    config = parse_topology_v2({"topology_v2": {}})
    assert config == TopologyV2Config()


def test_empty_lists_parse_to_empty_tuples() -> None:
    """All-six blocks present but empty parse to all-empty tuples."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "connectors": [],
                "credentials": [],
                "cc_pairs": [],
                "collections": [],
                "scope_profiles": [],
                "skills": [],
            }
        }
    )
    assert config.connectors == ()
    assert config.credentials == ()
    assert config.cc_pairs == ()
    assert config.collections == ()
    assert config.scope_profiles == ()
    assert config.skills == ()


def test_topology_v2_must_be_mapping_not_list() -> None:
    """``topology_v2:`` as a list (operator typo) raises a structural parse error."""
    with pytest.raises(TopologyV2ParseError) as excinfo:
        parse_topology_v2({"topology_v2": ["connectors", "credentials"]})
    assert "topology_v2" in str(excinfo.value)
    assert "fix:" in str(excinfo.value)


def test_single_connector_parses() -> None:
    """One connector block round-trips into a frozen ConnectorConfig."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "connectors": [
                    {
                        "id": "obsidian-personal",
                        "kind": "obsidian",
                        "name": "obsidian-personal",
                        "connector_specific_config": {"vault_root": "/data/vault"},
                        "refresh_freq_seconds": 300,
                        "default_sensitivity": "internal",
                    }
                ]
            }
        }
    )
    assert len(config.connectors) == 1
    c = config.connectors[0]
    assert isinstance(c, ConnectorConfig)
    assert c.id == "obsidian-personal"
    assert c.kind == "obsidian"
    assert c.refresh_freq_seconds == 300
    assert c.default_sensitivity == "internal"
    assert ("vault_root", "/data/vault") in c.connector_specific_config


def test_connector_extractor_fields_default_when_absent() -> None:
    """A connector without extractor keys defaults to passthrough + empty chain/config.

    D1 — the canonical ``ConnectorConfig`` carries the extractor selection
    that legacy entries supplied to ``build_extractor_from_entry``. Absent
    keys must default to the back-compat ``passthrough`` extractor with an
    empty chain and empty config so existing operator configs see no change.
    """
    config = parse_topology_v2(
        {
            "topology_v2": {
                "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            }
        }
    )
    c = config.connectors[0]
    assert c.extractor == "passthrough"
    assert c.extractor_chain == ()
    assert c.extractor_config == ()


def test_connector_extractor_fields_parse() -> None:
    """A connector declaring extractor/chain/config round-trips into the frozen shape.

    Mirrors the live SharePoint connector (``extractor: markitdown``) plus
    the optional ordered chain and tuple-of-pairs extractor_config the
    legacy registry path consumed.
    """
    config = parse_topology_v2(
        {
            "topology_v2": {
                "connectors": [
                    {
                        "id": "sp-conn",
                        "kind": "sharepoint",
                        "name": "sp-conn",
                        "extractor": "markitdown",
                        "extractor_chain": ["markitdown", "passthrough"],
                        "extractor_config": {"max_pages": "50", "ocr": "true"},
                    }
                ]
            }
        }
    )
    c = config.connectors[0]
    assert isinstance(c, ConnectorConfig)
    assert c.extractor == "markitdown"
    assert c.extractor_chain == ("markitdown", "passthrough")
    # extractor_config is a sorted tuple of (key, value-as-str) pairs (F42).
    assert c.extractor_config == (("max_pages", "50"), ("ocr", "true"))


def test_connector_extractor_config_must_be_mapping() -> None:
    """``connectors.*.extractor_config`` must be a mapping — string fails loud."""
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2(
            {
                "topology_v2": {
                    "connectors": [
                        {
                            "id": "c1",
                            "kind": "obsidian",
                            "name": "c1",
                            "extractor_config": "max_pages=50",
                        }
                    ]
                }
            }
        )


def test_connector_extractor_chain_must_be_list() -> None:
    """``connectors.*.extractor_chain`` must be a list — string fails loud."""
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2(
            {
                "topology_v2": {
                    "connectors": [
                        {
                            "id": "c1",
                            "kind": "obsidian",
                            "name": "c1",
                            "extractor_chain": "markitdown",
                        }
                    ]
                }
            }
        )


def test_single_credential_parses() -> None:
    """One credential block round-trips into a frozen CredentialConfig."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "credentials": [
                    {
                        "id": "ms-app-tenant",
                        "kind": "sharepoint",
                        "secret_name": "kv://kairix/sharepoint-app",  # pragma: allowlist secret
                        "user_id": None,
                        "admin_public": True,
                    }
                ]
            }
        }
    )
    assert len(config.credentials) == 1
    cred = config.credentials[0]
    assert isinstance(cred, CredentialConfig)
    assert cred.id == "ms-app-tenant"
    assert cred.admin_public is True


def test_cc_pair_parses_with_optional_credential_none() -> None:
    """A cc_pair without a credential parses (credential=None — local FS connectors)."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "cc_pairs": [
                    {
                        "id": "obsidian-personal-default",
                        "connector": "obsidian-personal",
                        "credential": None,
                        "name": "obsidian-personal-default",
                        "access_type": "PRIVATE",
                    }
                ]
            }
        }
    )
    pair = config.cc_pairs[0]
    assert isinstance(pair, CCPairConfig)
    assert pair.credential is None
    assert pair.access_type == "PRIVATE"


def test_collection_with_sources_parses() -> None:
    """A collection block with two sources parses into a Collection with two
    CollectionSourceConfig tuples."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "collections": [
                    {
                        "name": "client-x-engagement",
                        "sources": [
                            {"cc_pair": "obsidian-personal-default", "path_filter": "01-Projects/Client-X/*"},
                            {"cc_pair": "ms-team-sharepoint", "path_filter": "site:client-x/*"},
                        ],
                    }
                ]
            }
        }
    )
    col = config.collections[0]
    assert isinstance(col, CollectionConfig)
    assert col.name == "client-x-engagement"
    assert len(col.sources) == 2
    assert isinstance(col.sources[0], CollectionSourceConfig)
    assert col.sources[0].cc_pair == "obsidian-personal-default"


def test_collection_tier_defaults_to_none() -> None:
    """A collection without a ``tier`` key parses to ``tier=None``.

    D4 — the ranking ``tier`` is sourced from topology collections at
    build time. Absent ``tier`` means "no tier boost" (None) so existing
    operator configs see no ranking change.
    """
    config = parse_topology_v2(
        {
            "topology_v2": {
                "collections": [{"name": "obsidian-all", "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}]}]
            }
        }
    )
    assert config.collections[0].tier is None


def test_collection_tier_parses() -> None:
    """A collection declaring ``tier: reference`` round-trips into the frozen shape.

    D4 — the parser carries the operator-declared ranking tier onto
    ``CollectionConfig.tier`` so the (db-free) tier-map derivation can read
    it at pipeline-build time.
    """
    config = parse_topology_v2(
        {
            "topology_v2": {
                "collections": [
                    {
                        "name": "reflib",
                        "tier": "reference",
                        "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}],
                    }
                ]
            }
        }
    )
    col = config.collections[0]
    assert isinstance(col, CollectionConfig)
    assert col.tier == "reference"


def test_scope_profile_parses() -> None:
    """A scope_profile with two entries parses into the frozen aggregator."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "scope_profiles": [
                    {
                        "name": "team-shape-builder",
                        "actor_kind": "group",
                        "entries": [
                            {
                                "actor_id": "agent-shape",
                                "collection_name": "agent-shape/private-memory",
                                "mode": "read_write",
                            },
                            {
                                "actor_id": "agent-shape",
                                "collection_name": "client-x-engagement",
                                "mode": "read",
                            },
                        ],
                    }
                ]
            }
        }
    )
    profile = config.scope_profiles[0]
    assert isinstance(profile, ScopeProfileConfig)
    assert profile.actor_kind == "group"
    assert len(profile.entries) == 2
    assert isinstance(profile.entries[0], ScopeEntryConfig)
    assert profile.entries[0].mode == "read_write"


def test_skill_with_task_collections_parses() -> None:
    """A skill with nested task_collections + sources parses fully."""
    config = parse_topology_v2(
        {
            "topology_v2": {
                "skills": [
                    {
                        "name": "prepare-sow",
                        "task_collections": [
                            {
                                "name": "client-x-engagement",
                                "sources": [
                                    {
                                        "cc_pair": "obsidian-personal-default",
                                        "path_filter": "01-Projects/Client-X/*",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }
    )
    skill = config.skills[0]
    assert isinstance(skill, SkillConfig)
    assert isinstance(skill.task_collections[0], SkillTaskCollectionConfig)
    assert isinstance(skill.task_collections[0].sources[0], SkillSourceConfig)
    assert skill.task_collections[0].sources[0].cc_pair == "obsidian-personal-default"


def test_missing_required_field_raises_parse_error() -> None:
    """A connector missing ``name`` triggers an F21-shape parse error.

    Sabotage-proof: I temporarily replaced ``_require_str``'s strip
    check with ``isinstance(value, str)`` only — the test still passed
    because an empty string was rejected by argparse downstream. Then
    I dropped the entire ``_require_str`` call → test failed (no
    TopologyV2ParseError raised). Restored.
    """
    with pytest.raises(TopologyV2ParseError) as excinfo:
        parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "obsidian"}]}})
    assert "name" in str(excinfo.value).lower()
    assert "fix:" in str(excinfo.value)


def test_invalid_sensitivity_raises() -> None:
    """A connector with ``default_sensitivity=top-secret`` is rejected."""
    with pytest.raises(TopologyV2ParseError) as excinfo:
        parse_topology_v2(
            {
                "topology_v2": {
                    "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1", "default_sensitivity": "top-secret"}]
                }
            }
        )
    assert "top-secret" in str(excinfo.value) or "F39" in str(excinfo.value)


def test_chunk_sensitivity_vocabulary_normalises_to_f39_tiers() -> None:
    """Chunk-tag vocabulary values parse and map onto F39 tiers (GH #480).

    The example config (and every connector's ``sensitivity_for``) uses the
    ``Sensitivity`` literal; the parser accepts it and normalises via the
    same mapping the slack/m365 connectors use (client-confidential →
    confidential, personal → restricted).
    """
    cfg = parse_topology_v2(
        {
            "topology_v2": {
                "connectors": [
                    {"id": "c1", "kind": "github", "name": "c1", "default_sensitivity": "client-confidential"},
                    {"id": "c2", "kind": "obsidian", "name": "c2", "default_sensitivity": "personal"},
                ]
            }
        }
    )
    assert cfg.connectors[0].default_sensitivity == "confidential"
    assert cfg.connectors[1].default_sensitivity == "restricted"


def test_example_config_topology_block_parses_clean() -> None:
    """The repo's own example config must parse through topology_v2 (GH #480)."""
    import yaml

    example = Path(__file__).resolve().parents[2] / "kairix.config.example.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    cfg = parse_topology_v2(data)
    assert cfg is not None


def test_invalid_access_type_raises() -> None:
    """A cc_pair with ``access_type=WIDE_OPEN`` is rejected."""
    payload = {
        "topology_v2": {
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "p1", "access_type": "WIDE_OPEN"}]
        }
    }
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2(payload)


def test_invalid_actor_kind_raises() -> None:
    """A scope_profile with ``actor_kind=robot`` is rejected."""
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2({"topology_v2": {"scope_profiles": [{"name": "p", "actor_kind": "robot", "entries": []}]}})


def test_invalid_mode_raises() -> None:
    """A scope entry with ``mode=admin`` is rejected."""
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2(
            {
                "topology_v2": {
                    "scope_profiles": [
                        {
                            "name": "p",
                            "entries": [{"actor_id": "a", "collection_name": "c", "mode": "admin"}],
                        }
                    ]
                }
            }
        )


def test_collection_filters_must_be_list() -> None:
    """``cc_pairs.*.collection_filters`` must be a list — string fails loud."""
    payload = {
        "topology_v2": {
            "cc_pairs": [
                {
                    "id": "p1",
                    "connector": "c1",
                    "credential": None,
                    "name": "p1",
                    "collection_filters": "not-a-list",
                }
            ]
        }
    }
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2(payload)


def test_connector_specific_config_must_be_mapping() -> None:
    """``connectors.*.connector_specific_config`` must be a mapping."""
    payload = {
        "topology_v2": {
            "connectors": [
                {
                    "id": "c1",
                    "kind": "obsidian",
                    "name": "c1",
                    "connector_specific_config": "vault_root=/x",
                }
            ]
        }
    }
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2(payload)


def test_block_must_be_list_not_dict() -> None:
    """If the operator wrote ``connectors:`` as a mapping, the parser rejects."""
    with pytest.raises(TopologyV2ParseError):
        parse_topology_v2({"topology_v2": {"connectors": {"c1": {"kind": "obsidian", "name": "c1"}}}})


def test_top_level_legacy_keys_are_ignored() -> None:
    """Backward-compat: a legacy config with top-level ``connectors:`` AND no
    ``topology_v2:`` parent key parses to the empty config — the worker-side
    ``connectors:`` block (read by :mod:`kairix.worker`) is intentionally
    distinct from the Wave D topology v2 surface."""
    legacy_payload = {
        "connectors": [{"name": "obsidian", "config": {"vault_root": "/x"}}],
        "collections": {"shared": [{"name": "vault", "path": "/x"}]},
    }
    config = parse_topology_v2(legacy_payload)
    assert config == TopologyV2Config()
