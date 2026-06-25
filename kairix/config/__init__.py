"""Operator-facing config promotion surface (Wave D — topology v2).

This package owns the parsed, typed representation of the topology v2
operator-config blocks (``connectors:`` / ``credentials:`` / ``cc_pairs:`` /
``collections:`` / ``scope_profiles:`` / ``skills:``) plus the
cross-reference validators that fail loud on referential-integrity
drift.

Per ADR v2 §"Wave D" + the connector-scope-topology spec. The Wave A
schema lands the SQL tables; the Wave C runtime composes the data once
populated; Wave D is what turns a YAML file into populated rows + a
validate-before-apply pipeline.

Public surface:

* :class:`TopologyV2Config` — frozen aggregator over the 6 blocks.
* :func:`parse_topology_v2` — YAML-dict → :class:`TopologyV2Config`.
* :func:`validate_topology_v2_references` — referential-integrity gate.

Read-only: importing this module never reads the filesystem or env vars.
The :mod:`kairix.core.search.config_loader` resolver is responsible for
locating the YAML file; this module only parses + validates the dict it
receives.
"""

from __future__ import annotations

from kairix.config.topology_v2 import (
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
    config_pairs_to_mapping,
    parse_topology_v2,
)
from kairix.config.topology_v2_validators import (
    ValidationFailure,
    validate_topology_v2_references,
)

__all__ = [
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
    "TopologyV2Config",
    "ValidationFailure",
    "config_pairs_to_mapping",
    "parse_topology_v2",
    "validate_topology_v2_references",
]
