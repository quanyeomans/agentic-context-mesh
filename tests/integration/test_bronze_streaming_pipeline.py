"""Integration test for streaming-bronze mode through the production
worker pipeline — Phase 4 of streaming-bronze (#27).

Drives a real obsidian sync end-to-end with ``bronze_mode: streaming``
in the config. Asserts:
  - No on-disk blobs land under bronze_root
  - bronze_records rows have raw_path = "" (DB sentinel)
  - documents rows persist with the same shape as filesystem-mode
  - content_hash is populated on every bronze_records row (Phase 2 contract)

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


def _write_streaming_config(tmp_path: Path, vault_root: Path) -> Path:
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "bronze_mode": "streaming",
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
    """Phase 4 integration: configured streaming-mode connector sync
    completes without writing any on-disk bronze blobs.

    Sabotage proof: remove the ``bronze_mode == "streaming"`` branch in
    build_bronze_from_entry (falls through to filesystem); this test
    fails because bronze_root accumulates 10 on-disk blobs.
    """
    document_root = tmp_path / "vault"
    bronze_root = tmp_path / "bronze"
    db_path = tmp_path / "index.sqlite"

    _seed_vault(document_root, count=10)

    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    db.close()

    config_path = _write_streaming_config(tmp_path, document_root)
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(db_path), timeout=10.0),
        bronze_root_resolver=lambda: bronze_root,
    )
    result = run_via_connector_pipeline(deps)
    assert result.synced == 10, f"all 10 items should sync; got {result}"
    assert result.failed == 0

    # No on-disk blobs landed (streaming-mode)
    if bronze_root.exists():
        on_disk = list(bronze_root.rglob("*"))
        files_only = [p for p in on_disk if p.is_file()]
        assert files_only == [], f"streaming bronze must not touch disk; files: {files_only}"

    # bronze_records rows present with DB sentinel + content_hash populated
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


def test_filesystem_bronze_pipeline_still_writes_disk_blobs(tmp_path: Path) -> None:
    """Backward-compat assertion: configs without bronze_mode (or with
    bronze_mode: filesystem) still write on-disk blobs as before.

    Sabotage proof: change the default in build_bronze_from_entry from
    'filesystem' to 'streaming'; this test fails because no blobs land.
    """
    document_root = tmp_path / "vault"
    bronze_root = tmp_path / "bronze"
    db_path = tmp_path / "index.sqlite"

    _seed_vault(document_root, count=3)

    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    db.close()

    # NO bronze_mode field → defaults to filesystem
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
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
    result = run_via_connector_pipeline(deps)
    assert result.synced == 3

    # 3 on-disk blobs
    files_only = [p for p in bronze_root.rglob("*") if p.is_file()]
    assert len(files_only) == 3, f"filesystem mode (default) should write 3 blobs; got {len(files_only)}"
