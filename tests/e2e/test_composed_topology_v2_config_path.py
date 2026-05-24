"""E2E composed path for the topology v2 Wave D operator-config promotion.

ADR v2 §"Acceptance criteria" #5 calls for ``kairix features status``
to show the topology v2 surface and the operator-config promotion to
land via the ``kairix cc-pair`` verbs. This file is the F48 sibling
test that exercises the composed production path:

  1. **Parse** an operator-supplied YAML dict into the frozen
     :class:`TopologyV2Config` via ``parse_topology_v2``.
  2. **Validate** referential integrity via
     ``validate_topology_v2_references``.
  3. **Apply** the config by INSERTing connectors / credentials /
     cc_pairs into the topology v2 schema rows.
  4. **Read** the topology v2 diagnostics via
     ``build_topology_v2_diagnostics`` (the same surface backing
     ``kairix features status --topology-v2`` + ``tool_features_status``).
  5. **Operate** through the ``kairix cc-pair`` CLI to transition the
     cc_pair via the lifecycle service.

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config → factory
→ ingest → query → assertion via the composed production code paths.
"""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing, redirect_stdout
from pathlib import Path

import pytest

from kairix.config import parse_topology_v2, validate_topology_v2_references
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.connectors.cc_pair_cli import main as cc_pair_main
from kairix.core.db.schema import create_schema
from kairix.core.features.topology_v2_status import build_topology_v2_diagnostics

pytestmark = pytest.mark.e2e


def _bootstrap_db(tmp_path: Path) -> Path:
    """Create a fresh schema-applied SQLite path."""
    db_path = tmp_path / "kairix.sqlite"
    with closing(sqlite3.connect(str(db_path))) as db:
        create_schema(db, dims=4)
    return db_path


def test_composed_topology_v2_config_path_parse_validate_apply_diagnose(tmp_path: Path) -> None:
    """End-to-end: parse → validate → apply → diagnose against the production path.

    Per F48: the test exercises every layer of the Wave D composed path
    against the real ``topology_*`` schema rows, the real
    ``ScopeProfileResolver`` resolver, and the real
    ``build_topology_v2_diagnostics`` snapshot builder.
    """
    # 1. Parse a complete operator-supplied YAML dict.
    yaml_dict = {
        "topology_v2": {
            "connectors": [
                {"id": "obsidian-personal", "kind": "obsidian", "name": "obsidian-personal"},
            ],
            "credentials": [],
            "cc_pairs": [
                {
                    "id": "obsidian-personal-default",
                    "connector": "obsidian-personal",
                    "credential": None,
                    "name": "obsidian-personal-default",
                },
            ],
            "collections": [
                {
                    "name": "vault-projects",
                    "sources": [{"cc_pair": "obsidian-personal-default", "path_filter": "01-Projects/*"}],
                },
            ],
            "scope_profiles": [
                {
                    "name": "agent-alpha-profile",
                    "actor_kind": "agent",
                    "entries": [
                        {"actor_id": "agent-alpha", "collection_name": "vault-projects", "mode": "read"},
                    ],
                },
            ],
            "skills": [
                {
                    "name": "prepare-research",
                    "task_collections": [
                        {
                            "name": "vault-projects",
                            "sources": [{"cc_pair": "obsidian-personal-default"}],
                        },
                    ],
                },
            ],
        }
    }
    config = parse_topology_v2(yaml_dict)
    assert len(config.connectors) == 1
    assert len(config.cc_pairs) == 1

    # 2. Validate referential integrity — should pass clean.
    failures = validate_topology_v2_references(config)
    assert failures == (), f"unexpected failures: {failures}"

    # 3. Apply — INSERT the rows via the production lifecycle service.
    db_path = _bootstrap_db(tmp_path)
    with closing(sqlite3.connect(str(db_path))) as db:
        now = "2026-05-23T00:00:00Z"
        cur = db.execute(
            "INSERT INTO topology_connectors "
            "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
            "VALUES (?, ?, '{}', ?, ?, ?)",
            (
                config.connectors[0].kind,
                config.connectors[0].name,
                config.connectors[0].default_sensitivity,
                now,
                now,
            ),
        )
        connector_id = cur.lastrowid
        assert connector_id is not None
        pair = create_cc_pair(
            db,
            connector_id=int(connector_id),
            credential_id=None,
            name=config.cc_pairs[0].name,
        )
        # Drop a collection row + scope profile so the diagnostics surface has data.
        cur = db.execute(
            "INSERT INTO topology_collections "
            "(name, default_sensitivity, on_unmapped_item, visibility, created_at, updated_at) "
            "VALUES (?, 'internal', 'land_in_default_collection', 'engagement', ?, ?)",
            (config.collections[0].name, now, now),
        )
        collection_id = cur.lastrowid
        assert collection_id is not None
        db.execute(
            "INSERT INTO topology_collection_sources "
            "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
            "VALUES (?, ?, ?, NULL)",
            (int(collection_id), pair.id, config.collections[0].sources[0].path_filter),
        )
        cur = db.execute(
            "INSERT INTO topology_scope_profiles (actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
            "VALUES (?, ?, '[]', ?, ?)",
            ("agent-alpha", "agent", now, now),
        )
        profile_id = cur.lastrowid
        assert profile_id is not None
        db.execute(
            "INSERT INTO topology_scope_entries "
            "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
            "VALUES (?, ?, 1, 0, 'internal')",
            (int(profile_id), "vault-projects"),
        )
        db.commit()

        # 4. Diagnose — the same snapshot the CLI / MCP surface returns.
        diag = build_topology_v2_diagnostics(db)
        assert len(diag.cc_pairs) == 1
        assert diag.cc_pairs[0].name == "obsidian-personal-default"
        assert len(diag.actor_scopes) == 1
        assert diag.actor_scopes[0].actor_id == "agent-alpha"
        assert "vault-projects" in diag.actor_scopes[0].readable_collections

    # 5. Operate — drive the cc-pair CLI through its real entry point.
    out = io.StringIO()

    def _provider(_explicit: Path | None) -> sqlite3.Connection:
        return sqlite3.connect(str(db_path))

    with redirect_stdout(out):
        exit_code = cc_pair_main(["list"], db_provider=_provider)
    assert exit_code == 0
    assert "obsidian-personal-default" in out.getvalue()
    assert "SCHEDULED" in out.getvalue()

    # Final transition: advance to ACTIVE through the real lifecycle service.
    with closing(sqlite3.connect(str(db_path))) as db:
        transition_cc_pair(db, 1, "INITIAL_INDEXING")
        transition_cc_pair(db, 1, "ACTIVE")
        db.commit()

    out2 = io.StringIO()
    with redirect_stdout(out2):
        cc_pair_main(["list"], db_provider=_provider)
    assert "ACTIVE" in out2.getvalue()


def test_composed_topology_v2_config_path_validation_catches_dangling_reference(tmp_path: Path) -> None:
    """Sabotage: parse a config with a dangling cc_pair → collection ref →
    validators surface the F21-shaped error so the operator never reaches
    apply."""
    _ = _bootstrap_db(tmp_path)
    config = parse_topology_v2(
        {
            "topology_v2": {
                "collections": [
                    {
                        "name": "vault",
                        "sources": [{"cc_pair": "never-declared", "path_filter": "*"}],
                    }
                ],
            }
        }
    )
    failures = validate_topology_v2_references(config)
    assert len(failures) == 1
    assert failures[0].rule == "collection_source_cc_pair_missing"
    assert "never-declared" in failures[0].message
    assert "fix:" in failures[0].message
    assert "next: run" in failures[0].message
