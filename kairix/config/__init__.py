"""Operator-facing config promotion surface (Wave D — topology).

This package owns the parsed, typed representation of the topology
operator-config blocks (``connectors:`` / ``credentials:`` / ``cc_pairs:`` /
``collections:`` / ``scope_profiles:`` / ``skills:``) plus the
cross-reference validators that fail loud on referential-integrity
drift.

Per ADR v2 §"Wave D" + the connector-scope-topology spec. The Wave A
schema lands the SQL tables; the Wave C runtime composes the data once
populated; Wave D is what turns a YAML file into populated rows + a
validate-before-apply pipeline.

Public surface:

* :class:`TopologyConfig` — frozen aggregator over the 6 blocks.
* :func:`parse_topology` — YAML-dict → :class:`TopologyConfig`.
* :func:`validate_topology_references` — referential-integrity gate.

Read-only: importing this module never reads the filesystem or env vars.
The :mod:`kairix.core.search.config_loader` resolver is responsible for
locating the YAML file; this module only parses + validates the dict it
receives.
"""

from __future__ import annotations

from kairix.config.topology import (
    LEGACY_TOPOLOGY_CONFIG_KEY,
    TOPOLOGY_CONFIG_KEY,
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
from kairix.config.topology_validators import (
    ValidationFailure,
    validate_topology_references,
)

__all__ = [
    "LEGACY_TOPOLOGY_CONFIG_KEY",
    "TOPOLOGY_CONFIG_KEY",
    "CCPairConfig",
    "CollectionConfig",
    "CollectionSourceConfig",
    "ConnectorConfig",
    "CredentialConfig",
    "ScopeEntryConfig",
    "ScopeProfileConfig",
    "SkillConfig",
    "SkillSourceConfig",
    "SkillTaskCollectionConfig",
    "TopologyConfig",
    "ValidationFailure",
    "config_pairs_to_mapping",
    "normalize_topology_key",
    "parse_topology",
    "validate_topology_references",
]
