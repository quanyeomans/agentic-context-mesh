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

import pytest
import yaml

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


def _write_obsidian_config(tmp_path: Path, vault_root: Path) -> Path:
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "extractor": "passthrough",
                        "config": {"vault_root": str(vault_root)},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return cfg


def test_streaming_bronze_pipeline_writes_no_disk_blobs(tmp_path: Path) -> None:
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

    config_path = _write_obsidian_config(tmp_path, document_root)
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: config_path,
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
    """Phase 7 removed the bronze_mode config field. Configs that still
    carry it must surface a fix-pointer error at first connector
    resolution so operators see the failure at deploy time.

    Sabotage proof: drop the ``raise ValueError`` from build_bronze_from_entry;
    this test fails because the sync proceeds instead of failing with the
    fix-pointer message.
    """
    document_root = tmp_path / "vault"
    bronze_root = tmp_path / "bronze"
    db_path = tmp_path / "index.sqlite"

    _seed_vault(document_root, count=3)

    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    db.close()

    # Legacy config with the obsolete bronze_mode field
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "bronze_mode": "filesystem",
                        "extractor": "passthrough",
                        "config": {"vault_root": str(document_root)},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: cfg,
        db_factory=lambda: sqlite3.connect(str(db_path), timeout=10.0),
        bronze_root_resolver=lambda: bronze_root,
    )
    # Worker absorbs per-connector failures (logs warning, returns zero
    # synced for the failed connector). Sync reports 0 synced and 0 failed
    # because the failure happened at resolution before any item was processed.
    result = run_via_connector_pipeline(deps)
    assert result.synced == 0, f"legacy bronze_mode field should prevent sync; got {result}"
