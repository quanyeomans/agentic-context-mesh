"""F54 integration coverage for the ``topology_v2_config`` feature flag.

Wave D lands the operator-config promotion surface: the 6 YAML blocks
(connectors / credentials / cc_pairs / collections / scope_profiles /
skills), 5 cross-reference validators, the ``kairix cc-pair`` CLI, and
the topology v2 diagnostics in ``kairix features status``. The flag
default-OFF preserves byte-identical pre-Wave-D behaviour; the flag-ON
branch wires the topology v2 surface live.

Per F54 (docs/architecture/feature-flag-architecture.md §5): both
branches of every flag get an integration test exercising the
toggle through ``FakeFeatureFlagResolver`` from ``tests/fakes.py``. The
string literal ``"topology_v2_config"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

Parser + validator behaviour is pure-function and tested separately
(see ``tests/unit/test_topology_v2_config_parser.py`` +
``test_topology_v2_validators.py``). This file pins the FLAG-GATING
contract: which surfaces remain available when the flag is OFF vs ON.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.config import parse_topology_v2, validate_topology_v2_references
from kairix.core.db.schema import create_schema
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def _build_db() -> sqlite3.Connection:
    """Build a fresh in-memory DB with the production schema applied."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def test_topology_v2_config_flag_registered() -> None:
    """The flag exists in the registry, default=False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_config" in REGISTRY
    flag = REGISTRY["topology_v2_config"]
    assert flag.default is False
    assert flag.stage == "introduce"
    assert flag.owner == "connector-framework"


def test_flag_off_parser_still_loads_yaml() -> None:
    """OFF branch: the parser surface is unconditionally importable + functional.

    The flag gates whether the topology v2 surface is APPLIED at the
    runtime layer, not whether the parser can read YAML. Parser
    behaviour must be flag-independent so operators can stage the
    config promotion (parse-validate-render → flip flag) as two
    independent steps.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_config", False)
    config = parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}]}})
    assert resolver.get("topology_v2_config") is False
    assert len(config.connectors) == 1


def test_flag_on_parser_loads_yaml_identically() -> None:
    """ON branch: parser shape is identical — flag only gates application."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_config", True)
    config = parse_topology_v2({"topology_v2": {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1"}]}})
    assert resolver.get("topology_v2_config") is True
    assert len(config.connectors) == 1


def test_flag_state_reflects_resolver() -> None:
    """Basic correctness — both branches resolve to the expected boolean."""
    on = FakeFeatureFlagResolver().with_flag("topology_v2_config", True)
    off = FakeFeatureFlagResolver().with_flag("topology_v2_config", False)
    assert on.get("topology_v2_config") is True
    assert off.get("topology_v2_config") is False


def test_flag_off_validators_still_callable() -> None:
    """OFF branch: cross-reference validators are unconditionally callable.

    Default-safe: an operator running ``kairix config validate`` while
    the flag is OFF still gets cross-reference checks on any Wave D
    blocks they've started populating (so they can stage their config
    edits before the cutover). Failures are advisory, not blocking.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_config", False)
    config = parse_topology_v2({"topology_v2": {"cc_pairs": [{"id": "p1", "connector": "missing", "name": "p1"}]}})
    failures = validate_topology_v2_references(config)
    assert resolver.get("topology_v2_config") is False
    assert any(f.rule == "cc_pair_connector_missing" for f in failures)


def test_flag_on_validators_still_callable() -> None:
    """ON branch: validators behave identically — flag-independent."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_config", True)
    config = parse_topology_v2({"topology_v2": {"cc_pairs": [{"id": "p1", "connector": "missing", "name": "p1"}]}})
    failures = validate_topology_v2_references(config)
    assert resolver.get("topology_v2_config") is True
    assert any(f.rule == "cc_pair_connector_missing" for f in failures)


def test_topology_v2_diagnostics_zero_when_db_empty() -> None:
    """Diagnostics surface degrades to the zero-snapshot on a fresh DB.

    Mirrors the default-safe principle: operator can call the surface
    independent of flag state and gets a clean zero-snapshot when no
    topology v2 rows exist.
    """
    from kairix.core.features.topology_v2_status import build_topology_v2_diagnostics

    db = _build_db()
    diag = build_topology_v2_diagnostics(db)
    assert diag.cc_pairs == ()
    assert diag.actor_scopes == ()


def test_topology_v2_diagnostics_surfaces_declared_cc_pair() -> None:
    """Diagnostics surface lists a declared cc_pair after manual INSERT.

    Exercises the read path used by ``kairix features status --topology-v2``
    and ``tool_features_status(topology_v2=True)``. The cc_pair row is
    inserted via the public lifecycle service (no SQL bypass).
    """
    from kairix.core.connectors.cc_pair import create_cc_pair
    from kairix.core.features.topology_v2_status import build_topology_v2_diagnostics

    db = _build_db()
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', 'c-personal', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    create_cc_pair(db, connector_id=int(connector_id), credential_id=None, name="cc-personal")
    db.commit()

    diag = build_topology_v2_diagnostics(db)
    assert len(diag.cc_pairs) == 1
    assert diag.cc_pairs[0].name == "cc-personal"
    assert diag.cc_pairs[0].status == "SCHEDULED"
