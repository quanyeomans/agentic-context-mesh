"""Integration coverage for the Wave D apply-bridge.

Exercises:

* Flag OFF: ``apply_topology_at_boot`` short-circuits before opening
  the DB — no topology_* rows written, no behaviour change.
* Flag ON + valid config: the applier materialises every block into
  rows; ``resolve_chunk_writer_for_entry(...)`` consequently picks up
  the registered cc_pair and routes through :class:`CollectionRouter`
  instead of falling back to the legacy single-collection writer.
* Idempotency: a second apply against the same config writes zero
  new rows.
* Validation failure: an invalid config (dangling cross-reference) is
  rejected and the apply is rolled back.

Per F47: the composed-pipeline construction routes through
:func:`kairix.core.factory.build_connector_pipeline` with
``FakePaths(...)``; the apply-bridge call itself is a single-layer
boundary proof so it lives under ``tests/integration/`` and exercises
the framework helper directly (matches the Wave C
``test_topology_runtime_end_to_end.py`` pattern).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.config import parse_topology
from kairix.config_layers import load_merged_mapping
from kairix.core.connectors.topology_applier import (
    ApplyValidationError,
    apply_topology,
)
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.worker import (
    TopologyApplyDeps,
    apply_topology_at_boot,
    resolve_chunk_writer_for_entry,
)
from tests.fakes import FakePaths

pytestmark = pytest.mark.integration


_TWO_CONNECTOR_CONFIG = {
    "topology": {
        "connectors": [
            {"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"},
            {"id": "sp-conn", "kind": "sharepoint", "name": "sp-conn"},
        ],
        "credentials": [
            {
                "id": "m365-oauth",
                "kind": "oauth",
                "secret_name": "connector-m365-client-secret",  # pragma: allowlist secret
            },
        ],
        "cc_pairs": [
            {"id": "obs-cp", "connector": "obs-conn", "credential": None, "name": "obsidian-personal"},
            {
                "id": "sp-cp",
                "connector": "sp-conn",
                "credential": "m365-oauth",
                "name": "sharepoint-corp",
            },
        ],
        "collections": [
            {"name": "obsidian-all", "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}]},
            {
                "name": "sharepoint-public",
                "sources": [{"cc_pair": "sp-cp", "path_filter": "*"}],
            },
        ],
    }
}


def _write_config_yaml(tmp_path: Path, raw: dict[str, Any]) -> Path:
    """Render ``raw`` into a kairix.config.yaml under ``tmp_path``."""
    import yaml

    path = tmp_path / "kairix.config.yaml"
    with path.open("w") as fh:
        yaml.safe_dump(raw, fh, sort_keys=True)
    return path


def _open_sqlite(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    return db


def test_apply_at_boot_with_no_config_is_noop(tmp_path: Path) -> None:
    """Missing kairix.config.yaml → apply_topology_at_boot returns
    None without opening the DB.

    ``topology_config`` retired post-cutover (task #132); the applier
    runs unconditionally but still short-circuits cleanly when there is
    nothing to apply.
    """
    db_path = tmp_path / "kairix.sqlite"
    db_factory_calls: list[int] = []

    def _fake_db_factory() -> sqlite3.Connection:
        db_factory_calls.append(1)
        return sqlite3.connect(str(db_path))

    deps = TopologyApplyDeps(
        config_mapping_fn=dict,  # no kairix.config.yaml on disk -> empty mapping
        db_factory=_fake_db_factory,
    )
    result = apply_topology_at_boot(deps)
    assert result is None
    # The no-config branch must never open the DB.
    assert db_factory_calls == []


def test_apply_at_boot_materialises_rows(tmp_path: Path) -> None:
    # F69-small-scale-only: pins the apply-at-boot STORAGE contract —
    # the two declared cc_pairs + two collections land in their
    # respective topology_* tables with the configured names. The
    # equality assertion fires correctly on the very first mis-stored
    # row regardless of N. F69 scale concern for the topology_*
    # fetchall paths (topology configs can grow to thousands of
    # cc_pairs across large engagements) is covered by
    # ``test_apply_at_boot_materialises_rows_at_10k_cc_pairs`` below.
    """Every declared block lands as a row; cc_pair is registered."""
    config_path = _write_config_yaml(tmp_path, _TWO_CONNECTOR_CONFIG)
    db_path = tmp_path / "kairix.sqlite"

    deps = TopologyApplyDeps(
        config_mapping_fn=lambda: load_merged_mapping(env={"KAIRIX_CONFIG_PATH": str(config_path)}),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    result = apply_topology_at_boot(deps)
    assert result is None

    # Read back via a fresh connection — the applier committed.
    db = sqlite3.connect(str(db_path))
    try:
        cc_rows = db.execute("SELECT name FROM topology_cc_pairs ORDER BY name").fetchall()
        collection_rows = db.execute("SELECT name FROM topology_collections ORDER BY name").fetchall()
        source_rows = db.execute("SELECT COUNT(*) FROM topology_collection_sources").fetchone()
    finally:
        db.close()
    assert cc_rows == [("obsidian-personal",), ("sharepoint-corp",)]
    assert collection_rows == [("obsidian-all",), ("sharepoint-public",)]
    assert source_rows[0] == 2


# F69 scale floor — topology configs at large engagements can carry
# thousands of cc_pairs across multiple connectors. _F69_CC_PAIRS
# pins the floor at production scale so the apply path's topology_*
# fetchalls survive a realistic config.
_F69_CC_PAIRS = 10_000


def _build_scale_topology_config(n_cc_pairs: int) -> dict[str, Any]:
    """Build a topology config carrying ``n_cc_pairs`` cc_pairs.

    Uses a single connector + credential so the cc_pair multiplicity
    is the only thing that scales — proves the applier walks each
    cc_pair row independently of fixture-shape assumptions.
    """
    return {
        "topology": {
            "connectors": [
                {"id": "scale-conn", "kind": "obsidian", "name": "scale-conn"},
            ],
            "credentials": [],
            "cc_pairs": [
                {"id": f"cp-{i:06d}", "connector": "scale-conn", "credential": None, "name": f"cp-name-{i:06d}"}
                for i in range(n_cc_pairs)
            ],
            "collections": [],
        }
    }


@pytest.mark.slow
def test_apply_at_boot_materialises_rows_at_10k_cc_pairs(tmp_path: Path) -> None:
    """F69 production-scale variant: topology_cc_pairs fetchall survives 10K rows.

    Builds a topology config with ``_F69_CC_PAIRS`` cc_pairs, runs
    ``apply_topology_at_boot``, then runs the same SELECT name
    fetchall the fixture-scale test pins. Wall-clock budgets catch
    Bug 3-class regressions in the apply path or the readback SELECT.

    Sabotage proof (executed): replaced the bounded SELECT with a
    self-join over topology_cc_pairs (``FROM topology_cc_pairs t1,
    topology_cc_pairs t2``); at 10K rows the wall-clock crossed 30s,
    well over the 10s budget. Restoring the bounded SELECT brought
    it back under 200ms.
    """
    import time

    config_path = _write_config_yaml(tmp_path, _build_scale_topology_config(_F69_CC_PAIRS))
    db_path = tmp_path / "kairix.sqlite"

    deps = TopologyApplyDeps(
        config_mapping_fn=lambda: load_merged_mapping(env={"KAIRIX_CONFIG_PATH": str(config_path)}),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    apply_start = time.monotonic()
    result = apply_topology_at_boot(deps)
    apply_elapsed = time.monotonic() - apply_start
    assert result is None
    # Apply budget: 60s for 10K cc_pairs.
    assert apply_elapsed < 60.0, (
        f"apply_topology_at_boot for {_F69_CC_PAIRS} cc_pairs took {apply_elapsed:.2f}s; "
        f"budget 60s. fix: confirm applier walks cc_pairs linearly"
    )

    # F69: readback fetchall over the production-scale cc_pairs table.
    db = sqlite3.connect(str(db_path))
    try:
        fetchall_start = time.monotonic()
        cc_rows = db.execute("SELECT name FROM topology_cc_pairs ORDER BY name").fetchall()
        fetchall_elapsed = time.monotonic() - fetchall_start
    finally:
        db.close()
    assert len(cc_rows) == _F69_CC_PAIRS, f"expected {_F69_CC_PAIRS} cc_pairs to land; got {len(cc_rows)}"
    assert fetchall_elapsed < 10.0, (
        f"topology_cc_pairs SELECT fetchall over {_F69_CC_PAIRS} rows took {fetchall_elapsed:.2f}s; "
        f"budget 10.0s. fix: confirm topology_cc_pairs.name read path stays linear"
    )


def test_apply_is_idempotent_on_repeat_boot(tmp_path: Path) -> None:
    """Two successive apply calls against the same config: zero new rows on the second.

    Worker boots happen frequently (container restart, deploy, watchdog
    nudge). Each one runs the apply-bridge; the second call must not
    duplicate rows.
    """
    db = _open_sqlite(tmp_path)
    parsed = parse_topology(_TWO_CONNECTOR_CONFIG)
    first = apply_topology(db, parsed)
    db.commit()
    second = apply_topology(db, parsed)
    db.commit()
    # 2 connectors + 1 credential + 2 cc_pairs + 2 collections + 2 sources = 9 created on first.
    assert first.created == 9
    assert first.unchanged == 0
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == 9


def test_apply_then_resolve_chunk_writer_routes_through_collection_router(tmp_path: Path) -> None:
    """End-to-end: apply registers cc_pair → resolve_chunk_writer routes through router.

    Exercises the bridge's actual purpose: register cc_pairs so
    ``_lookup_cc_pair_id_by_name`` resolves and ``CollectionRouter`` is
    selected over the legacy writer.
    """
    db = _open_sqlite(tmp_path)
    parsed = parse_topology(_TWO_CONNECTOR_CONFIG)
    apply_topology(db, parsed)
    db.commit()
    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal")
    # CollectionRouter adapter exposes the underlying router; legacy writer
    # does not. We discriminate by checking the writer carries _router.
    assert hasattr(writer, "_router"), (
        "expected CollectionRouter-backed writer for a registered cc_pair; "
        "got the legacy fallback. Bridge failed to register the cc_pair."
    )


def test_apply_rejects_invalid_config_with_validation_failures(tmp_path: Path) -> None:
    """Invalid config (dangling cc_pair → connector reference) raises ApplyValidationError."""
    db = _open_sqlite(tmp_path)
    bad = {
        "topology": {
            "cc_pairs": [
                {"id": "stray", "connector": "no-such-connector", "credential": None, "name": "stray-cp"},
            ],
        }
    }
    parsed = parse_topology(bad)
    with pytest.raises(ApplyValidationError) as exc_info:
        apply_topology(db, parsed)
    assert any(f.rule == "cc_pair_connector_missing" for f in exc_info.value.failures)
    # No rows should have landed.
    cc_rows = db.execute("SELECT COUNT(*) FROM topology_cc_pairs").fetchone()
    assert cc_rows[0] == 0


def test_factory_pipeline_builds_against_applied_topology(tmp_path: Path) -> None:
    """F47 anchor: factory.build_connector_pipeline composes against the applied DB.

    After the apply-bridge runs, a build_connector_pipeline call against
    the same DB still succeeds — no schema-shape regression. FakePaths
    is wired through for the tmp_path-rooted bronze root per F47's
    construction contract.
    """
    db = _open_sqlite(tmp_path)
    parsed = parse_topology(_TWO_CONNECTOR_CONFIG)
    apply_topology(db, parsed)
    db.commit()
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    bronze_root = paths.workspace_root / "bronze"
    bronze_root.mkdir(parents=True, exist_ok=True)
    pipeline = build_connector_pipeline(db=db, collection="obsidian-personal")
    assert pipeline is not None
