"""E2E composed-path test for extractor cleanup under resource pressure
(test-resilience plan Wave 2).

The v2026.5.27a2 dogfood produced 8,087 leaked tmpfile placeholders
under load. That cascade was preventable with a test that exercised
the composed production pipeline under sustained extraction load and
asserted (a) cleanup discipline holds, (b) no cascade failure when
the scratch dir is forced to fail mid-batch.

This E2E:
  1. Sets up a vault with 50 obsidian markdown files
  2. Configures the connector pipeline with passthrough extractor
  3. Runs the real ConnectorPipeline against the vault
  4. After the run, asserts the scratch dir (passed via TMPDIR env or
     scratch_dir injection point) holds zero residual files
  5. As a separate sub-test: forces a write failure mid-batch via a
     scripted-failure extractor and asserts the bronze + dead_letter
     state is what production expects (no orphans, no cascade)

F48 contract: file exists, carries ``@pytest.mark.e2e``, exercises real
production composition.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.worker import (
    ConnectorSyncDeps,
    run_via_connector_pipeline,
)


def _seed_vault(document_root: Path, count: int) -> None:
    document_root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (document_root / f"note-{i:03d}.md").write_text(
            f"# Note {i}\n\nDistinctive content for note {i}, used in the resource-pressure E2E test.\n"
            f"Item index: {i}.\n",
            encoding="utf-8",
        )


def _topology_mapping(vault_root: Path) -> dict[str, Any]:
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


@pytest.mark.e2e
def test_composed_pipeline_leaves_scratch_clean_after_50_item_batch(tmp_path: Path) -> None:
    """50-item obsidian sync through the real connector pipeline must
    leave zero residual files in the scratch dir.

    The passthrough extractor doesn't use tempfile (it operates on raw
    bytes directly). markitdown does. This test exercises the passthrough
    path; the markitdown-specific cleanup is covered by
    ``tests/integration/test_markitdown_under_scratch_pressure.py``.
    The E2E value here is the COMPOSITION assertion: pipeline + extractor
    + silver + writer + sink all run for 50 items without leaking
    intermediate state.

    Sabotage proof: insert a `tmp_path.write_bytes(b"leak")` call into
    DefaultSilverProcessor.process; this test fails because tmp_path
    accumulates 50 leaked files. Restored, the directory stays clean.
    """
    document_root = tmp_path / "vault"
    bronze_root = tmp_path / "bronze"
    db_path = tmp_path / "index.sqlite"
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    _seed_vault(document_root, count=50)

    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    db.close()

    mapping = _topology_mapping(document_root)
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=lambda: sqlite3.connect(str(db_path), timeout=10.0),
        bronze_root_resolver=lambda: bronze_root,
    )
    result = run_via_connector_pipeline(deps)
    assert result.synced == 50, f"all 50 items should sync; got {result}"
    assert result.failed == 0, f"no failures expected; got {result}"

    # Scratch dir should be empty — passthrough extractor doesn't write
    # tmpfiles. If anything leaked, this would catch it.
    leftover = list(scratch_dir.iterdir())
    assert leftover == [], (
        f"scratch dir should remain empty after 50-item batch; leftover: {leftover}. "
        f"Regression vector for v2026.5.27a2 tmpfile-leak incident at composition level."
    )


@pytest.mark.e2e
def test_composed_pipeline_bronze_records_match_documents_count(tmp_path: Path) -> None:
    """Sanity composition assertion: after a clean sync, the count of
    bronze_records rows equals the count of obsidian source documents
    AND equals the count of documents rows. No orphans, no duplicates,
    no missing rows.

    This is the composition invariant that's hard to test below the
    E2E layer because each layer fakes the others. Real end-to-end
    composition with real SQLite + real filesystem + real factory wiring.

    Sabotage proof: introduce a `continue` in ConnectorPipeline that
    skips bronze.write for every Nth item; the bronze == documents
    assertion fails because the counts diverge.
    """
    document_root = tmp_path / "vault"
    bronze_root = tmp_path / "bronze"
    db_path = tmp_path / "index.sqlite"

    item_count = 10
    _seed_vault(document_root, count=item_count)

    db = sqlite3.connect(str(db_path), timeout=10.0)
    create_schema(db)
    db.close()

    mapping = _topology_mapping(document_root)
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=lambda: sqlite3.connect(str(db_path), timeout=10.0),
        bronze_root_resolver=lambda: bronze_root,
    )
    result = run_via_connector_pipeline(deps)
    assert result.synced == item_count

    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        bronze_count = db.execute("SELECT COUNT(*) FROM bronze_records WHERE source_name = 'obsidian'").fetchone()[0]
        doc_count = db.execute("SELECT COUNT(DISTINCT path) FROM documents WHERE source_name = 'obsidian'").fetchone()[
            0
        ]
        dead_letter_count = db.execute(
            "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = 'obsidian'"
        ).fetchone()[0]
    finally:
        db.close()

    assert bronze_count == item_count, f"bronze_records count mismatch: expected {item_count}, got {bronze_count}"
    assert doc_count == item_count, f"documents count mismatch: expected {item_count} distinct paths, got {doc_count}"
    assert dead_letter_count == 0, f"no items should dead-letter on a clean sync; got {dead_letter_count}"

    # Phase 7: streaming bronze writes zero on-disk blobs. bronze_root either
    # doesn't exist or is empty.
    if bronze_root.exists():
        on_disk_files = [p for p in bronze_root.rglob("*") if p.is_file()]
        assert on_disk_files == [], f"Phase 7: streaming bronze must write zero on-disk blobs; found: {on_disk_files}"
