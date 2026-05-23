"""Unit tests for the Wave D topology v2 cross-reference validators.

Sabotage proofs (mutate → confirm fail → restore — see commit body for
the full proof log) — one per rule:

* Rule 1 (cc_pair_connector_missing): commented out the
  ``if pair.connector not in connector_ids`` branch in
  ``_validate_cc_pair_refs`` → ``test_cc_pair_unknown_connector_flagged``
  failed (no failure was returned for the missing connector ref).
  Restored.
* Rule 2 (cc_pair_credential_missing): replaced the credential check
  with ``if False`` → ``test_cc_pair_unknown_credential_flagged`` failed.
  Restored.
* Rule 3 (collection_source_cc_pair_missing): swapped ``not in`` for
  ``in`` (inverted check) → ``test_collection_source_unknown_cc_pair_flagged``
  failed because no failure was reported (every cc_pair "matched" the
  empty set under the inverted predicate). Restored.
* Rule 4 (scope_profile_entry_collection_missing): replaced the inner
  ``if entry.collection_name not in collection_names`` body with
  ``continue`` → ``test_scope_profile_entry_unknown_collection_flagged``
  failed (no failure surfaced). Restored.
* Rule 5 (skill_source_cc_pair_missing): commented out the entire
  inner loop body in ``_validate_skill_source_refs`` →
  ``test_skill_source_unknown_cc_pair_flagged`` failed. Restored.

All 5 mutations confirmed the tests pin the validator logic. None of
the mutations needed assertion edits to flip — the tests are sabotage-
proof on the production-side path.
"""

from __future__ import annotations

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
    validate_topology_v2_references,
)

pytestmark = pytest.mark.unit


def _connector(cid: str = "c1") -> ConnectorConfig:
    return ConnectorConfig(id=cid, kind="obsidian", name=cid)


def _credential(cid: str = "cred1") -> CredentialConfig:
    return CredentialConfig(id=cid, kind="api_key", secret_name="kv://x")  # pragma: allowlist secret


def _cc_pair(
    *,
    pid: str = "p1",
    connector: str = "c1",
    credential: str | None = "cred1",
) -> CCPairConfig:
    return CCPairConfig(id=pid, connector=connector, credential=credential, name=pid)


def test_empty_config_returns_no_failures() -> None:
    """All-empty config has nothing to validate."""
    failures = validate_topology_v2_references(TopologyV2Config())
    assert failures == ()


def test_fully_consistent_config_returns_no_failures() -> None:
    """A config where every reference points at a declared entry passes."""
    config = TopologyV2Config(
        connectors=(_connector("c1"),),
        credentials=(_credential("cred1"),),
        cc_pairs=(_cc_pair(),),
        collections=(
            CollectionConfig(
                name="vault-projects",
                sources=(CollectionSourceConfig(cc_pair="p1", path_filter="*"),),
            ),
        ),
        scope_profiles=(
            ScopeProfileConfig(
                name="agent-alpha",
                entries=(ScopeEntryConfig(actor_id="agent-alpha", collection_name="vault-projects"),),
            ),
        ),
        skills=(
            SkillConfig(
                name="prepare-sow",
                task_collections=(
                    SkillTaskCollectionConfig(
                        name="vault",
                        sources=(SkillSourceConfig(cc_pair="p1"),),
                    ),
                ),
            ),
        ),
    )
    assert validate_topology_v2_references(config) == ()


# ---------------------------------------------------------------------------
# Rule 1 — cc_pairs.*.connector
# ---------------------------------------------------------------------------


def test_cc_pair_unknown_connector_flagged() -> None:
    """A cc_pair referencing a missing connector trips rule 1."""
    config = TopologyV2Config(
        connectors=(),
        cc_pairs=(_cc_pair(connector="ghost"),),
    )
    failures = validate_topology_v2_references(config)
    assert any(f.rule == "cc_pair_connector_missing" for f in failures)
    rule_failure = next(f for f in failures if f.rule == "cc_pair_connector_missing")
    assert "ghost" in rule_failure.message
    assert "fix:" in rule_failure.message
    assert rule_failure.location == "cc_pairs[0].connector"


# ---------------------------------------------------------------------------
# Rule 2 — cc_pairs.*.credential
# ---------------------------------------------------------------------------


def test_cc_pair_unknown_credential_flagged() -> None:
    """A cc_pair referencing a missing credential trips rule 2."""
    config = TopologyV2Config(
        connectors=(_connector("c1"),),
        credentials=(),
        cc_pairs=(_cc_pair(connector="c1", credential="ghost-cred"),),
    )
    failures = validate_topology_v2_references(config)
    assert any(f.rule == "cc_pair_credential_missing" for f in failures)
    rule_failure = next(f for f in failures if f.rule == "cc_pair_credential_missing")
    assert "ghost-cred" in rule_failure.message
    assert "fix:" in rule_failure.message


def test_cc_pair_with_none_credential_does_not_trigger_rule_2() -> None:
    """``credential: null`` is valid (local-FS connectors) — not flagged."""
    config = TopologyV2Config(
        connectors=(_connector("c1"),),
        cc_pairs=(_cc_pair(connector="c1", credential=None),),
    )
    failures = validate_topology_v2_references(config)
    assert all(f.rule != "cc_pair_credential_missing" for f in failures)


# ---------------------------------------------------------------------------
# Rule 3 — collections.*.sources.*.cc_pair
# ---------------------------------------------------------------------------


def test_collection_source_unknown_cc_pair_flagged() -> None:
    """A collection source referencing a missing cc_pair trips rule 3."""
    config = TopologyV2Config(
        collections=(
            CollectionConfig(
                name="vault",
                sources=(CollectionSourceConfig(cc_pair="ghost-pair"),),
            ),
        ),
    )
    failures = validate_topology_v2_references(config)
    assert any(f.rule == "collection_source_cc_pair_missing" for f in failures)
    rule_failure = next(f for f in failures if f.rule == "collection_source_cc_pair_missing")
    assert "ghost-pair" in rule_failure.message
    assert rule_failure.location == "collections[0].sources[0].cc_pair"


# ---------------------------------------------------------------------------
# Rule 4 — scope_profiles.*.entries.*.collection_name
# ---------------------------------------------------------------------------


def test_scope_profile_entry_unknown_collection_flagged() -> None:
    """A scope_profile entry referencing a missing collection trips rule 4."""
    config = TopologyV2Config(
        collections=(),
        scope_profiles=(
            ScopeProfileConfig(
                name="agent-alpha",
                entries=(ScopeEntryConfig(actor_id="agent-alpha", collection_name="ghost-collection"),),
            ),
        ),
    )
    failures = validate_topology_v2_references(config)
    assert any(f.rule == "scope_profile_entry_collection_missing" for f in failures)
    rule_failure = next(f for f in failures if f.rule == "scope_profile_entry_collection_missing")
    assert "ghost-collection" in rule_failure.message
    assert "fix:" in rule_failure.message


# ---------------------------------------------------------------------------
# Rule 5 — skills.*.task_collections.*.sources.*.cc_pair
# ---------------------------------------------------------------------------


def test_skill_source_unknown_cc_pair_flagged() -> None:
    """A skill source referencing a missing cc_pair trips rule 5."""
    config = TopologyV2Config(
        skills=(
            SkillConfig(
                name="prepare-sow",
                task_collections=(
                    SkillTaskCollectionConfig(
                        name="vault",
                        sources=(SkillSourceConfig(cc_pair="ghost-skill-pair"),),
                    ),
                ),
            ),
        ),
    )
    failures = validate_topology_v2_references(config)
    assert any(f.rule == "skill_source_cc_pair_missing" for f in failures)
    rule_failure = next(f for f in failures if f.rule == "skill_source_cc_pair_missing")
    assert "ghost-skill-pair" in rule_failure.message
    assert "fix:" in rule_failure.message


# ---------------------------------------------------------------------------
# Aggregation — multiple failures, deterministic order
# ---------------------------------------------------------------------------


def test_multiple_failures_returned_in_deterministic_order() -> None:
    """Sort key is (rule, location); failures are stably ordered."""
    config = TopologyV2Config(
        cc_pairs=(
            _cc_pair(pid="p-a", connector="ghost", credential="ghost-cred"),
            _cc_pair(pid="p-b", connector="ghost", credential=None),
        ),
        collections=(
            CollectionConfig(
                name="vault",
                sources=(CollectionSourceConfig(cc_pair="ghost-pair"),),
            ),
        ),
    )
    failures = validate_topology_v2_references(config)
    # Multiple failures expected
    assert len(failures) >= 3
    sorted_keys = [(f.rule, f.location) for f in failures]
    assert sorted_keys == sorted(sorted_keys)


def test_failure_message_carries_f21_affordance() -> None:
    """Every failure message contains ``fix:`` AND ``next: run`` per F21 spirit."""
    config = TopologyV2Config(cc_pairs=(_cc_pair(connector="ghost"),))
    failures = validate_topology_v2_references(config)
    for f in failures:
        assert "fix:" in f.message
        assert "next: run" in f.message
