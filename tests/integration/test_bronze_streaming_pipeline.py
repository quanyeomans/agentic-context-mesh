"""Integration test for streaming-bronze pipeline (post-Phase-7 #27).

Drives a real obsidian sync end-to-end. Streaming bronze is the only
persistence model — no on-disk blobs ever land, bronze_records rows
carry the empty raw_path sentinel + populated content_hash.

F47-clean: drives through ``run_via_connector_pipeline`` (real
production helper).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.worker import ConnectorSyncDeps, run_via_connector_pipeline

pytestmark = pytest.mark.integration


def _seed_vault(document_root: Path, count: int) -> None:
    document_root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (document_root / f"note-{i:03d}.md").write_text(
            f"# Note {i}\n\nbody content for streaming-bronze integration test.\n",
            encoding="utf-8",
        )


def _obsidian_topology_mapping(vault_root: Path) -> dict[str, Any]:
    """Canonical ``topology.connectors`` + cc_pair mapping for an obsidian
    connector — the read shape ingest enumerates after the Task 4
    canonical-collapse redirect (no legacy top-level ``connectors:``).
    """
    return {
        "topology_v2": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Obsidian Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault_root)},
                }
            ],
            "cc_pairs": [{"id": "obsidian-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian"}],
        }
    }


def test_streaming_bronze_pipeline_writes_no_disk_blobs(tmp_path: Path) -> None:
    # F69-small-scale-only: structural-contract test — the
    # ``raw_path==""`` sentinel + 64-char content_hash invariants fire on
    # row 1 regardless of N. The real obsidian connector here uses
    # watchdog/fsevents which (a) cap at per_tick_max_items=500 per F66
    # and (b) cannot re-attach to the same vault path across ticks
    # (fsevents raises "watch is already scheduled"). Driving 10K events
    # through this pipeline would force multi-tick orchestration that
    # adds wall-clock without changing the contract under test. The Bug 3
    # scale concern for bronze_records reads is covered by the canonical
    # 10K-row tests in test_bronze_records_scan_at_scale.py.
    """Phase 7 contract: connector sync writes zero on-disk bronze blobs.

    Sabotage proof: re-introduce FilesystemBronzeStore in worker.py
    and route bronze writes through it; this test fails because
    bronze_root accumulates 10 on-disk blobs.
    """
    document_root = tmp_path / "vault"
    bronze_root = tmp_path / "bronze"
    db_path = tmp_path / "index.sqlite"

    _seed_vault(document_root, count=10)

    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    db.close()

    mapping = _obsidian_topology_mapping(document_root)
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=lambda: sqlite3.connect(str(db_path), timeout=10.0),
        bronze_root_resolver=lambda: bronze_root,
    )
    result = run_via_connector_pipeline(deps)
    assert result.synced == 10, f"all 10 items should sync; got {result}"
    assert result.failed == 0

    # No on-disk blobs land (streaming bronze is the only mode)
    if bronze_root.exists():
        files_only = [p for p in bronze_root.rglob("*") if p.is_file()]
        assert files_only == [], f"Phase 7: streaming bronze must not touch disk; files: {files_only}"

    # bronze_records rows present with the empty-string DB sentinel + content_hash
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        rows = db.execute(
            "SELECT item_id, raw_path, content_hash FROM bronze_records WHERE source_name = 'obsidian'"
        ).fetchall()
    finally:
        db.close()
    assert len(rows) == 10
    for item_id, raw_path, content_hash in rows:
        assert raw_path == "", f"{item_id}: streaming raw_path should be empty sentinel, got {raw_path!r}"
        assert content_hash and len(content_hash) == 64, (
            f"{item_id}: content_hash should be 64-char SHA-256 hex, got {content_hash!r}"
        )


def test_legacy_bronze_mode_field_fails_fast(tmp_path: Path) -> None:
    """Phase 7 removed the bronze_mode config field. An entry that still
    carries it must surface a fix-pointer error at bronze-store
    construction so operators see the failure at deploy time.

    Driven through the public ``build_bronze_from_entry`` boundary — the
    canonical Task 4 ingest entry never carries a top-level ``bronze_mode``
    key, so the guard is exercised at the registry boundary it protects,
    not via a legacy top-level ``connectors:`` config block (that read
    path is gone).

    Sabotage proof: drop the ``raise ValueError`` from
    build_bronze_from_entry; this test fails because no ValueError is
    raised.
    """
    from kairix.core.connectors.registry import build_bronze_from_entry

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    try:
        with pytest.raises(ValueError, match="bronze_mode"):
            build_bronze_from_entry({"name": "obsidian", "bronze_mode": "filesystem"}, db=db)
    finally:
        db.close()
