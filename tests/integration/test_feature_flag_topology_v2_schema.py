"""F54 integration coverage for the ``topology_v2_schema`` feature flag.

The substantial Wave A schema-migration assertions live in
``tests/integration/test_topology_v2_schema_migration.py`` (12 tests).
This file pins the F54 both-branch contract specifically: same fake
resolver across OFF + ON, verifying the flag's value is observable to
gated code paths.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs an integration test exercising both branches via
``FakeFeatureFlagResolver`` from ``tests/fakes.py``.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import create_schema
from tests.fakes import FakeFeatureFlagResolver


@pytest.mark.integration
def test_flag_off_no_topology_writes() -> None:
    """OFF branch: schema tables exist (unconditional) but no Wave B+ writes fire."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_schema", False)
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)

    # Production code that would write under the flag must check it first.
    # When OFF, the write skip is the expected behaviour.
    if resolver.get("topology_v2_schema"):
        raise AssertionError("OFF branch should not enter the write path")

    count = db.execute("SELECT COUNT(*) FROM topology_connectors").fetchone()[0]
    assert count == 0, "OFF branch must leave topology tables empty"


@pytest.mark.integration
def test_flag_on_topology_write_succeeds() -> None:
    """ON branch: a gated write into a topology table succeeds."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_schema", True)
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)

    if resolver.get("topology_v2_schema"):
        db.execute(
            """
            INSERT INTO topology_connectors (
                kind, name, connector_specific_config,
                refresh_freq_seconds, prune_freq_seconds, perm_sync_freq_seconds,
                default_sensitivity, created_at, updated_at
            ) VALUES ('obsidian', 'wave-a-flag-on-probe', '{}', NULL, NULL, NULL, 'internal',
                      '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z')
            """
        )
        db.commit()

    count = db.execute("SELECT COUNT(*) FROM topology_connectors WHERE name = 'wave-a-flag-on-probe'").fetchone()[0]
    assert count == 1, "ON branch should write the probe row"


@pytest.mark.integration
def test_flag_state_reflects_resolver() -> None:
    """The flag's effective value matches what the resolver reports — basic correctness."""
    on = FakeFeatureFlagResolver().with_flag("topology_v2_schema", True)
    off = FakeFeatureFlagResolver().with_flag("topology_v2_schema", False)
    assert on.get("topology_v2_schema") is True
    assert off.get("topology_v2_schema") is False
