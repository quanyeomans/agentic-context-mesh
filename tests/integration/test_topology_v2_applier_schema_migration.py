"""Integration coverage for the canonical-collapse Phase 1 ``tier`` column.

Task 1 of the connector canonical-collapse refactor adds a ranking
``tier`` to the canonical collection model across three layers:

* config dataclass + parser (``kairix.config.topology_v2``)
* persistence schema (``topology_collections.tier``)
* apply-bridge (``topology_v2_applier`` INSERT + load-bearing UPDATE)

This module proves the schema + applier limbs:

* Fresh DB carries the ``tier TEXT`` column.
* Legacy DB (synthesised pre-tier shape) gains ``tier`` via the additive
  ALTER TABLE migration without data loss.
* The migration is idempotent (a second ``migrate()`` is a no-op).
* **Rollback (F79):** dropping the additive column restores the legacy
  shape and a re-``migrate()`` re-adds it cleanly — the additive change
  is reversible.
* The applier writes ``tier`` on INSERT and re-writes it in place on
  UPDATE (the previously no-op "always unchanged" branch is now
  load-bearing).
* **F84/F87 round-trip:** an operator config carrying ``tier:`` written
  to disk, read back through the canonical layered reader
  (``load_merged_mapping``), parsed and applied, lands the same ``tier``
  on the row — proving the write→read contract end to end.

Per F47: the apply-bridge is a single-layer boundary proof exercised
directly (matches ``test_topology_v2_applier.py``). The layered-read
round-trip injects the resolver's ``env`` mapping explicitly (F2: no
``KAIRIX_*`` setenv).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from kairix.config import parse_topology_v2
from kairix.config_layers import load_merged_mapping
from kairix.core.connectors.topology_v2_applier import ApplierDeps, apply_topology_v2
from kairix.core.db.schema import create_schema, migrate, validate_schema

pytestmark = pytest.mark.integration

_TABLE = "topology_collections"
_FIXED_NOW = "2026-06-21T00:00:00Z"


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    # safe: table is a closed-set constant from this module
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _tier_config(tier: str | None) -> dict[str, Any]:
    """A minimal valid topology_v2 mapping with one connector/cc_pair/collection."""
    collection: dict[str, Any] = {
        "name": "reflib",
        "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}],
    }
    if tier is not None:
        collection["tier"] = tier
    return {
        "topology_v2": {
            "connectors": [{"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"}],
            "cc_pairs": [{"id": "obs-cp", "connector": "obs-conn", "credential": None, "name": "obsidian-personal"}],
            "collections": [collection],
        }
    }


def _read_tier(db: sqlite3.Connection, name: str) -> str | None:
    row = db.execute("SELECT tier FROM topology_collections WHERE name = ?", (name,)).fetchone()
    assert row is not None, f"collection {name!r} not found"
    return row[0]


def _deterministic_deps() -> ApplierDeps:
    return ApplierDeps(now_fn=lambda: _FIXED_NOW)


def test_fresh_db_topology_collections_has_tier_column() -> None:
    """create_schema on a fresh DB creates the ``tier`` column."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    assert "tier" in _columns(db, _TABLE), "topology_collections.tier missing on fresh DB"


def test_legacy_db_gains_tier_via_additive_migration() -> None:
    """A pre-tier topology_collections table gains ``tier`` without data loss."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    # Synthesise the pre-tier shape: drop + recreate without the tier column,
    # then seed a probe row.
    db.execute(f"DROP TABLE {_TABLE}")
    db.execute(
        f"""
        CREATE TABLE {_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            default_sensitivity TEXT NOT NULL DEFAULT 'internal',
            on_unmapped_item TEXT NOT NULL DEFAULT 'land_in_default_collection',
            visibility TEXT NOT NULL DEFAULT 'engagement',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        f"INSERT INTO {_TABLE} (name, created_at, updated_at) VALUES (?, ?, ?)",
        ("legacy-collection", _FIXED_NOW, _FIXED_NOW),
    )
    db.commit()
    assert "tier" not in _columns(db, _TABLE)

    migrate(db)

    assert "tier" in _columns(db, _TABLE), "tier column not added by migration"
    # Legacy row preserved + back-compat NULL tier.
    assert _read_tier(db, "legacy-collection") is None
    assert validate_schema(db) == []


def test_tier_migration_is_idempotent() -> None:
    """Running migrate() twice does not error or duplicate the tier column."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    migrate(db)
    migrate(db)
    cols = [row[1] for row in db.execute(f"PRAGMA table_info({_TABLE})")]
    assert cols.count("tier") == 1, "tier column duplicated by idempotent migrate"


def test_tier_column_rollback_then_remigrate(tmp_path: Path) -> None:
    """F79 rollback — the additive ``tier`` column is reversible.

    SQLite supports DROP COLUMN since 3.35; the additive migration can be
    rolled back by dropping the column, restoring the legacy shape, and a
    fresh ``migrate()`` re-adds it cleanly with no data loss on the
    surviving rows.
    """
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    db.execute(
        f"INSERT INTO {_TABLE} (name, tier, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("reflib", "reference", _FIXED_NOW, _FIXED_NOW),
    )
    db.commit()
    assert "tier" in _columns(db, _TABLE)

    # Rollback: drop the additive column.
    db.execute(f"ALTER TABLE {_TABLE} DROP COLUMN tier")
    db.commit()
    assert "tier" not in _columns(db, _TABLE)
    # Surviving (non-tier) data intact post-rollback.
    surviving = db.execute(f"SELECT name FROM {_TABLE} WHERE name = ?", ("reflib",)).fetchone()
    assert surviving is not None and surviving[0] == "reflib"

    # Re-apply the forward migration — column comes back, no error.
    migrate(db)
    assert "tier" in _columns(db, _TABLE)
    assert validate_schema(db) == []
    # The dropped tier value is gone (NULL) — rollback is destructive on the
    # column's data by design; the row itself survives.
    assert _read_tier(db, "reflib") is None


def test_applier_writes_tier_on_insert() -> None:
    """The applier INSERTs the operator-declared ``tier`` onto a new row."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    parsed = parse_topology_v2(_tier_config("reference"))

    result = apply_topology_v2(db, parsed, applier_deps=_deterministic_deps())

    assert result.created >= 1
    assert _read_tier(db, "reflib") == "reference"


def test_applier_insert_defaults_tier_null_when_absent() -> None:
    """A collection without ``tier:`` lands NULL — back-compat default."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    parsed = parse_topology_v2(_tier_config(None))

    apply_topology_v2(db, parsed, applier_deps=_deterministic_deps())

    assert _read_tier(db, "reflib") is None


def test_applier_update_branch_rewrites_tier_in_place() -> None:
    """Editing a collection's ``tier:`` re-writes the existing row (load-bearing UPDATE)."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    deps = _deterministic_deps()

    apply_topology_v2(db, parse_topology_v2(_tier_config("reference")), applier_deps=deps)
    assert _read_tier(db, "reflib") == "reference"

    # Operator promotes the tier; the second apply must UPDATE in place.
    second = apply_topology_v2(db, parse_topology_v2(_tier_config("primary")), applier_deps=deps)

    assert second.updated >= 1, "tier change must report an UPDATE, not 'unchanged'"
    assert _read_tier(db, "reflib") == "primary"
    # No duplicate row created on UPDATE.
    count = db.execute("SELECT COUNT(*) FROM topology_collections WHERE name = ?", ("reflib",)).fetchone()[0]
    assert count == 1


def test_applier_idempotent_tier_reports_unchanged() -> None:
    """Re-applying the same tier reports 'unchanged' — the diff guard holds."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    deps = _deterministic_deps()
    config = _tier_config("reference")

    apply_topology_v2(db, parse_topology_v2(config), applier_deps=deps)
    second = apply_topology_v2(db, parse_topology_v2(config), applier_deps=deps)

    assert second.updated == 0, "unchanged tier must not report an UPDATE"
    assert _read_tier(db, "reflib") == "reference"


def test_tier_config_write_read_apply_round_trip(tmp_path: Path) -> None:
    """F84/F87 — write a tier-bearing config to disk, read it back through the
    canonical layered reader, apply, and confirm the tier lands on the row.

    Proves the full operator write→read contract: the layered reader
    (``load_merged_mapping``) is the same path the worker's boot apply
    flows through (#492). ``env`` is injected explicitly so the resolver
    points at the tmp config without any ``KAIRIX_*`` setenv (F2).
    """
    config_path = tmp_path / "kairix.config.yaml"
    with config_path.open("w") as fh:
        yaml.safe_dump(_tier_config("reference"), fh, sort_keys=True)

    # Canonical layered reader, env-injected (legacy single-file mode).
    mapping = load_merged_mapping(env={"KAIRIX_CONFIG_PATH": str(config_path)})
    assert "topology_v2" in mapping, "layered reader did not surface the topology_v2 block"

    parsed = parse_topology_v2(mapping)
    assert parsed.collections[0].tier == "reference"

    db = sqlite3.connect(str(tmp_path / "kairix.sqlite"))
    create_schema(db, dims=4)
    apply_topology_v2(db, parsed, applier_deps=_deterministic_deps())

    assert _read_tier(db, "reflib") == "reference"
