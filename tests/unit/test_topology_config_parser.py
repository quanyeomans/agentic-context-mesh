"""Unit tests for the Wave D topology YAML parser.

Covers :func:`kairix.config.parse_topology` — every block, every
edge (empty, missing required field, wrong list/dict shape, unknown
enum value). Sabotage-prove: see commit body for the 5 mutate→fail→
restore proofs per validator (this file primarily covers parse shape;
the validator file ships its own sabotage proofs).

Post #305: every payload nests the six Wave D blocks under a single
``topology:`` parent key so the ``collections:`` block stops
colliding with the legacy top-level ``collections.shared`` dict shape.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from kairix.config import (
    LEGACY_TOPOLOGY_CONFIG_KEY,
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
    TopologyConfig,
    config_pairs_to_mapping,
    normalize_topology_key,
    parse_topology,
)
from kairix.config.topology import TopologyParseError

pytestmark = pytest.mark.unit


def test_empty_dict_parses_to_all_empty_config() -> None:
    """Backward-compat: a legacy YAML without a topology block parses cleanly."""
    config = parse_topology({})
    assert config == TopologyConfig()


def test_explicit_null_topology_parses_to_all_empty_config() -> None:
    """``topology: null`` (operator declaring the key but leaving it empty) parses cleanly."""
    config = parse_topology({"topology": None})
    assert config == TopologyConfig()


def test_empty_topology_mapping_parses_to_all_empty_config() -> None:
    """``topology: {}`` (empty mapping) parses cleanly."""
    config = parse_topology({"topology": {}})
    assert config == TopologyConfig()


def test_empty_lists_parse_to_empty_tuples() -> None:
    """All-six blocks present but empty parse to all-empty tuples."""
    config = parse_topology(
        {
            "topology": {
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


def test_topology_must_be_mapping_not_list() -> None:
    """``topology:`` as a list (operator typo) raises a structural parse error."""
    with pytest.raises(TopologyParseError) as excinfo:
        parse_topology({"topology": ["connectors", "credentials"]})
    assert "topology" in str(excinfo.value)
    assert "fix:" in str(excinfo.value)


def test_single_connector_parses() -> None:
    """One connector block round-trips into a frozen ConnectorConfig."""
    config = parse_topology(
        {
            "topology": {
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
    assert config_pairs_to_mapping(c.connector_specific_config) == {"vault_root": "/data/vault"}


def test_connector_specific_config_preserves_nested_drives() -> None:
    """Nested connector config (SharePoint ``drives:``) round-trips as a list.

    Regression: ``_parse_connector_specific_config`` previously ``str()``-coerced
    every value, turning the ``drives`` list into a Python-repr string. The
    SharePoint connector factory then rejected it every sync tick with
    "'drives' must be a non-empty list", stalling the pipeline. JSON encoding
    preserves the structure so the materialized value is a real list.
    """
    drives = [
        {
            "site_id": "contoso.sharepoint.com,aaaa,bbbb",
            "exclude_paths": ["/Archive", "/Personal"],
        }
    ]
    config = parse_topology(
        {
            "topology": {
                "connectors": [
                    {
                        "id": "sp",
                        "kind": "sharepoint",
                        "name": "sp",
                        "connector_specific_config": {"drives": drives},
                    }
                ]
            }
        }
    )
    c = config.connectors[0]
    mapping = config_pairs_to_mapping(c.connector_specific_config)
    assert mapping["drives"] == drives
    assert isinstance(mapping["drives"], list)
    assert isinstance(mapping["drives"][0], dict)


def test_connector_specific_config_preserves_scalar_types() -> None:
    """Scalar config values round-trip to their real Python types (not str-coerced).

    Restores the pre-topology contract that connector/extractor factories
    receive raw YAML types. ``str()`` coercion (the bug) turned every value into
    a string; the JSON round-trip preserves int/bool/float as themselves.
    """
    config = parse_topology(
        {
            "topology": {
                "connectors": [
                    {
                        "id": "c",
                        "kind": "obsidian",
                        "name": "c",
                        "connector_specific_config": {
                            "max_items": 50,
                            "recursive": True,
                            "ratio": 1.5,
                        },
                    }
                ]
            }
        }
    )
    mapping = config_pairs_to_mapping(config.connectors[0].connector_specific_config)
    assert mapping == {"max_items": 50, "recursive": True, "ratio": 1.5}
    assert isinstance(mapping["max_items"], int)
    assert isinstance(mapping["recursive"], bool)


def test_connector_specific_config_date_values_do_not_crash() -> None:
    """YAML date values (JSON-unserializable) fall back to str, never crash.

    Regression: ``json.dumps`` without ``default=`` raises ``TypeError`` on
    ``datetime.date`` — which YAML 1.1 produces from bare ``2026-06-01`` scalars
    — crashing ``parse_topology`` and dropping every connector for the tick.
    ``default=str`` makes the encode total.
    """
    config = parse_topology(
        {
            "topology": {
                "connectors": [
                    {
                        "id": "c",
                        "kind": "obsidian",
                        "name": "c",
                        "connector_specific_config": {"valid_from": datetime.date(2026, 6, 1)},
                    }
                ]
            }
        }
    )
    mapping = config_pairs_to_mapping(config.connectors[0].connector_specific_config)
    assert mapping == {"valid_from": "2026-06-01"}


def test_config_pairs_to_mapping_empty_and_invalid() -> None:
    """Empty pairs → ``{}``; a non-JSON value raises a loud ValueError naming the key."""
    assert config_pairs_to_mapping(()) == {}
    with pytest.raises(ValueError, match="legacy_key"):
        config_pairs_to_mapping((("legacy_key", "[{'not': 'json'}]"),))


def test_connector_extractor_fields_default_when_absent() -> None:
    """A connector without extractor keys defaults to passthrough + empty chain/config.

    D1 — the canonical ``ConnectorConfig`` carries the extractor selection
    that legacy entries supplied to ``build_extractor_from_entry``. Absent
    keys must default to the back-compat ``passthrough`` extractor with an
    empty chain and empty config so existing operator configs see no change.
    """
    config = parse_topology(
        {
            "topology": {
                "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}],
            }
        }
    )
    c = config.connectors[0]
    assert c.extractor == "passthrough"
    assert c.extractor_chain == ()
    assert c.extractor_config == ()
    assert c.extractor_chain_configs == ()


def test_connector_extractor_fields_parse() -> None:
    """A connector declaring extractor/chain/config round-trips into the frozen shape.

    Mirrors the live SharePoint connector (``extractor: markitdown``) plus
    the optional ordered chain and tuple-of-pairs extractor_config the
    legacy registry path consumed.
    """
    config = parse_topology(
        {
            "topology": {
                "connectors": [
                    {
                        "id": "sp-conn",
                        "kind": "sharepoint",
                        "name": "sp-conn",
                        "extractor": "markitdown",
                        "extractor_chain": ["markitdown", "passthrough"],
                        "extractor_config": {"max_pages": "50", "ocr": "true"},
                        "extractor_chain_configs": {
                            "gotenberg": {
                                "config": {
                                    "gotenberg_url": "http://gotenberg:3000",
                                    "timeout_s": 30,
                                }
                            }
                        },
                    }
                ]
            }
        }
    )
    c = config.connectors[0]
    assert isinstance(c, ConnectorConfig)
    assert c.extractor == "markitdown"
    assert c.extractor_chain == ("markitdown", "passthrough")
    # extractor_config is a sorted tuple of (key, json-value) pairs (F42);
    # read it back through the per-connector boundary materializer.
    assert config_pairs_to_mapping(c.extractor_config) == {"max_pages": "50", "ocr": "true"}
    assert config_pairs_to_mapping(c.extractor_chain_configs) == {
        "gotenberg": {
            "config": {
                "gotenberg_url": "http://gotenberg:3000",
                "timeout_s": 30,
            }
        }
    }


def test_connector_extractor_config_must_be_mapping() -> None:
    """``connectors.*.extractor_config`` must be a mapping — string fails loud."""
    with pytest.raises(TopologyParseError):
        parse_topology(
            {
                "topology": {
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


def test_connector_extractor_chain_configs_must_be_mapping() -> None:
    """``connectors.*.extractor_chain_configs`` must be a mapping — list fails loud."""
    with pytest.raises(TopologyParseError):
        parse_topology(
            {
                "topology": {
                    "connectors": [
                        {
                            "id": "c1",
                            "kind": "sharepoint",
                            "name": "c1",
                            "extractor_chain_configs": ["gotenberg"],
                        }
                    ]
                }
            }
        )


def test_connector_extractor_chain_must_be_list() -> None:
    """``connectors.*.extractor_chain`` must be a list — string fails loud."""
    with pytest.raises(TopologyParseError):
        parse_topology(
            {
                "topology": {
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
    config = parse_topology(
        {
            "topology": {
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
    config = parse_topology(
        {
            "topology": {
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
    config = parse_topology(
        {
            "topology": {
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
    config = parse_topology(
        {
            "topology": {
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
    config = parse_topology(
        {
            "topology": {
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


def test_collection_retrieval_overrides_default_to_none() -> None:
    """A collection without a ``retrieval`` key parses to
    ``retrieval_overrides=None``.

    Canonical-collapse — per-collection retrieval tuning is sourced from
    the topology collection's ``retrieval:`` block at resolution time.
    Absent ``retrieval`` means "no per-collection override" (None) so the
    resolver falls back to the global retrieval config.
    """
    config = parse_topology(
        {
            "topology": {
                "collections": [{"name": "obsidian-all", "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}]}]
            }
        }
    )
    assert config.collections[0].retrieval_overrides is None


def test_collection_retrieval_overrides_parse() -> None:
    """A collection declaring a ``retrieval:`` block carries the raw nested
    dict onto ``CollectionConfig.retrieval_overrides``.

    Canonical-collapse — the override dict is consumed verbatim by
    ``merge_retrieval_config`` (the same shape the legacy
    ``collections.shared[*].retrieval`` block produced), so the resolver
    can apply reflib-style tuning sourced from topology.
    """
    config = parse_topology(
        {
            "topology": {
                "collections": [
                    {
                        "name": "reflib",
                        "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}],
                        "retrieval": {
                            "fusion_strategy": "bm25_primary",
                            "bm25_limit": 20,
                            "vec_limit": 5,
                        },
                    }
                ]
            }
        }
    )
    col = config.collections[0]
    assert isinstance(col, CollectionConfig)
    assert col.retrieval_overrides == {
        "fusion_strategy": "bm25_primary",
        "bm25_limit": 20,
        "vec_limit": 5,
    }


def test_scope_profile_parses() -> None:
    """A scope_profile with two entries parses into the frozen aggregator."""
    config = parse_topology(
        {
            "topology": {
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
    config = parse_topology(
        {
            "topology": {
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
    TopologyParseError raised). Restored.
    """
    with pytest.raises(TopologyParseError) as excinfo:
        parse_topology({"topology": {"connectors": [{"id": "c1", "kind": "obsidian"}]}})
    assert "name" in str(excinfo.value).lower()
    assert "fix:" in str(excinfo.value)


def test_invalid_sensitivity_raises() -> None:
    """A connector with ``default_sensitivity=top-secret`` is rejected."""
    with pytest.raises(TopologyParseError) as excinfo:
        parse_topology(
            {
                "topology": {
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
    cfg = parse_topology(
        {
            "topology": {
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
    """The repo's own example config must parse through topology (GH #480)."""
    import yaml

    example = Path(__file__).resolve().parents[2] / "kairix.config.example.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    cfg = parse_topology(data)
    assert cfg is not None


def test_invalid_access_type_raises() -> None:
    """A cc_pair with ``access_type=WIDE_OPEN`` is rejected."""
    payload = {
        "topology": {
            "cc_pairs": [{"id": "p1", "connector": "c1", "credential": None, "name": "p1", "access_type": "WIDE_OPEN"}]
        }
    }
    with pytest.raises(TopologyParseError):
        parse_topology(payload)


def test_invalid_actor_kind_raises() -> None:
    """A scope_profile with ``actor_kind=robot`` is rejected."""
    with pytest.raises(TopologyParseError):
        parse_topology({"topology": {"scope_profiles": [{"name": "p", "actor_kind": "robot", "entries": []}]}})


def test_invalid_mode_raises() -> None:
    """A scope entry with ``mode=admin`` is rejected."""
    with pytest.raises(TopologyParseError):
        parse_topology(
            {
                "topology": {
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
        "topology": {
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
    with pytest.raises(TopologyParseError):
        parse_topology(payload)


def test_connector_specific_config_must_be_mapping() -> None:
    """``connectors.*.connector_specific_config`` must be a mapping."""
    payload = {
        "topology": {
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
    with pytest.raises(TopologyParseError):
        parse_topology(payload)


def test_block_must_be_list_not_dict() -> None:
    """If the operator wrote ``connectors:`` as a mapping, the parser rejects."""
    with pytest.raises(TopologyParseError):
        parse_topology({"topology": {"connectors": {"c1": {"kind": "obsidian", "name": "c1"}}}})


def test_top_level_legacy_keys_are_ignored() -> None:
    """Backward-compat: a legacy config with top-level ``connectors:`` AND no
    ``topology:`` parent key parses to the empty config — the worker-side
    ``connectors:`` block (read by :mod:`kairix.worker`) is intentionally
    distinct from the Wave D topology surface."""
    legacy_payload = {
        "connectors": [{"name": "obsidian", "config": {"vault_root": "/x"}}],
        "collections": {"shared": [{"name": "vault", "path": "/x"}]},
    }
    config = parse_topology(legacy_payload)
    assert config == TopologyConfig()


def test_legacy_parent_key_normalizes_to_topology() -> None:
    """PLA-287: a config still keyed on the pre-rename parent key parses.

    The parent key was renamed (see :data:`LEGACY_TOPOLOGY_CONFIG_KEY`);
    ``normalize_topology_key`` surfaces the old key as ``topology`` on read
    so configs written before the rename keep resolving. The key string is
    referenced via the public constant, never hard-coded here.
    """
    payload = {LEGACY_TOPOLOGY_CONFIG_KEY: {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}]}}
    config = parse_topology(payload)
    assert config.connectors == (ConnectorConfig(id="c1", kind="obsidian", name="c1"),)


def test_normalize_prefers_canonical_key_when_both_present() -> None:
    """A wizard-migrated config carrying BOTH keys reads the canonical block.

    ``topology`` wins over the stale legacy block, so a partially-migrated
    file never orphans the fresh sources back onto the old key.
    """
    canonical = {"connectors": [{"id": "new", "kind": "slack", "name": "new"}]}
    legacy = {"connectors": [{"id": "old", "kind": "obsidian", "name": "old"}]}
    normalized = normalize_topology_key({"topology": canonical, LEGACY_TOPOLOGY_CONFIG_KEY: legacy})
    assert normalized["topology"] == canonical
    assert parse_topology({"topology": canonical, LEGACY_TOPOLOGY_CONFIG_KEY: legacy}).connectors[0].kind == "slack"
