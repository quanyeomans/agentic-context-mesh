"""Step definitions for topology_applier.feature (apply-bridge).

F1-clean: no @patch on kairix internals.
F46-clean: step impls compose via ``kairix.core.factory.build_*`` /
``kairix.core.connectors.topology_applier.apply_topology``
(call-graph depth ≤ 2). No direct ``ConnectorPipeline(...)`` construction.

``topology_config`` retired post-cutover (task #132); the apply step
now runs unconditionally at boot.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest
from pytest_bdd import given, scenarios, then, when

from kairix.config import parse_topology
from kairix.core.connectors.topology_applier import ApplyResult, apply_topology
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.bdd

# Bind every scenario in the .feature file to this step module.
scenarios("../features/topology_applier.feature")


_SAMPLE_CONFIG = {
    "topology": {
        "connectors": [
            {"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"},
        ],
        "credentials": [
            {"id": "m365-oauth", "kind": "oauth", "secret_name": "m365-secret"},  # pragma: allowlist secret
        ],
        "cc_pairs": [
            {"id": "obs-cp", "connector": "obs-conn", "credential": None, "name": "obs-personal"},
        ],
        "collections": [
            {
                "name": "obs-all",
                "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}],
            },
        ],
    }
}


@dataclass
class _ApplierCtx:
    """Per-scenario context — no module-level mutable state."""

    db: sqlite3.Connection | None = None
    first_result: ApplyResult | None = None
    second_result: ApplyResult | None = None


@pytest.fixture
def topology_applier_ctx() -> _ApplierCtx:
    return _ApplierCtx()


@given(
    "the operator has declared a topology config with one connector, one credential, one cc_pair, and one collection"
)
def _operator_declares_config(topology_applier_ctx: _ApplierCtx) -> None:
    # ``topology_config`` retired post-cutover (task #132); the applier
    # now runs unconditionally at boot.
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    topology_applier_ctx.db = db


@when("the operator runs the apply-bridge against an empty database")
def _operator_runs_apply(topology_applier_ctx: _ApplierCtx) -> None:
    assert topology_applier_ctx.db is not None
    parsed = parse_topology(_SAMPLE_CONFIG)
    topology_applier_ctx.first_result = apply_topology(topology_applier_ctx.db, parsed)
    topology_applier_ctx.db.commit()


@when("the operator runs the apply-bridge a second time against the same database")
def _operator_runs_apply_second(topology_applier_ctx: _ApplierCtx) -> None:
    assert topology_applier_ctx.db is not None
    parsed = parse_topology(_SAMPLE_CONFIG)
    topology_applier_ctx.second_result = apply_topology(topology_applier_ctx.db, parsed)
    topology_applier_ctx.db.commit()


@then("the apply reports one connector, one credential, one cc_pair, and two collection-shape rows as created")
def _apply_reports_created(topology_applier_ctx: _ApplierCtx) -> None:
    # 1 connector + 1 credential + 1 cc_pair + 1 collection + 1 collection_source = 5 created.
    result = topology_applier_ctx.first_result
    assert result is not None
    assert result.created == 5
    assert result.updated == 0
    assert result.unchanged == 0


@then("the second apply reports zero rows created and every row as unchanged")
def _apply_reports_unchanged(topology_applier_ctx: _ApplierCtx) -> None:
    result = topology_applier_ctx.second_result
    assert result is not None
    assert result.created == 0
    assert result.updated == 0
    assert result.unchanged == 5
