"""Integration coverage for the Wave D apply-bridge.

Exercises:

* Flag OFF: ``apply_topology_v2_at_boot`` short-circuits before opening
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
``test_topology_v2_runtime_end_to_end.py`` pattern).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.config import parse_topology_v2
from kairix.core.connectors.topology_v2_applier import (
    ApplyValidationError,
    apply_topology_v2,
)
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.worker import (
    TopologyV2ApplyDeps,
    apply_topology_v2_at_boot,
    resolve_chunk_writer_for_entry,
)
from tests.fakes import FakePaths

pytestmark = pytest.mark.integration


_TWO_CONNECTOR_CONFIG = {
    "topology_v2": {
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


def test_flag_off_apply_at_boot_is_noop(tmp_path: Path) -> None:
    """Flag OFF: apply_topology_v2_at_boot returns True without opening the DB.

    Default-safe principle — when the operator hasn't promoted the
    ``topology_v2_config`` flag yet, the worker boot path is bit-for-bit
    identical to today. The applier shouldn't read the config, shouldn't
    touch the DB, shouldn't even glance at the YAML.
    """
    config_path = _write_config_yaml(tmp_path, _TWO_CONNECTOR_CONFIG)
    db_path = tmp_path / "kairix.sqlite"
    db_factory_calls: list[int] = []

    def _fake_db_factory() -> sqlite3.Connection:
        db_factory_calls.append(1)
        return sqlite3.connect(str(db_path))

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: False,
        config_path_resolver=lambda: config_path,
        db_factory=_fake_db_factory,
    )
    ok = apply_topology_v2_at_boot(deps)
    assert ok is True
    # The flag-off branch must never open the DB.
    assert db_factory_calls == []


def test_flag_on_apply_at_boot_materialises_rows(tmp_path: Path) -> None:
    """Flag ON: every declared block lands as a row; cc_pair is registered."""
    config_path = _write_config_yaml(tmp_path, _TWO_CONNECTOR_CONFIG)
    db_path = tmp_path / "kairix.sqlite"

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    ok = apply_topology_v2_at_boot(deps)
    assert ok is True

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


def test_apply_is_idempotent_on_repeat_boot(tmp_path: Path) -> None:
    """Two successive apply calls against the same config: zero new rows on the second.

    Worker boots happen frequently (container restart, deploy, watchdog
    nudge). Each one runs the apply-bridge; the second call must not
    duplicate rows.
    """
    db = _open_sqlite(tmp_path)
    parsed = parse_topology_v2(_TWO_CONNECTOR_CONFIG)
    first = apply_topology_v2(db, parsed)
    db.commit()
    second = apply_topology_v2(db, parsed)
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
    parsed = parse_topology_v2(_TWO_CONNECTOR_CONFIG)
    apply_topology_v2(db, parsed)
    db.commit()
    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal", flag_on=True)
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
        "topology_v2": {
            "cc_pairs": [
                {"id": "stray", "connector": "no-such-connector", "credential": None, "name": "stray-cp"},
            ],
        }
    }
    parsed = parse_topology_v2(bad)
    with pytest.raises(ApplyValidationError) as exc_info:
        apply_topology_v2(db, parsed)
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
    parsed = parse_topology_v2(_TWO_CONNECTOR_CONFIG)
    apply_topology_v2(db, parsed)
    db.commit()
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    bronze_root = paths.workspace_root / "bronze"
    bronze_root.mkdir(parents=True, exist_ok=True)
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="obsidian-personal")
    assert pipeline is not None
