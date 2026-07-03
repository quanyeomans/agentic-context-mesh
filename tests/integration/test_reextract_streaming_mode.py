"""Integration tests for streaming-mode re-extract via connector.fetch
(Phase 5 of streaming-bronze, #27).

When a dead_letter row points at a streaming-bronze record (raw_path
is None), the re-extract path must re-fetch the raw bytes from source
via ``connector.fetch(item_id)`` rather than reading from on-disk
bronze (which has no bytes to read).

F47-clean: drives ``run_reextract_dead_letter`` directly with a real
StreamingBronzeStore-seeded DB.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors import DeadLetterStore, StreamingBronzeStore
from kairix.core.db.schema import create_schema
from kairix.worker import ReextractResult, run_reextract_dead_letter

pytestmark = pytest.mark.integration


def _streaming_obsidian_mapping(vault: Path) -> dict[str, Any]:
    """Canonical topology mapping for a streaming-bronze obsidian connector.

    Task 5: re-extract reads ``topology`` (cc_pair name matches the
    dead_letter ``source_name``; kind resolves the plugin), not the legacy
    top-level ``connectors:`` list.
    """
    return {
        "topology": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Personal Obsidian Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                }
            ],
            "cc_pairs": [
                {
                    "id": "obsidian-pair",
                    "connector": "obsidian-conn",
                    "credential": None,
                    "name": "obsidian",
                }
            ],
        }
    }


def test_reextract_streaming_row_recovers_via_connector_fetch(tmp_path: Path) -> None:
    """A dead_letter row with a streaming-bronze record (raw_path NULL)
    recovers by re-fetching the source file via connector.fetch.

    Sabotage proof: remove the ``if ref.raw_path is None`` branch in
    _reextract_rows; the test fails because bronze.read raises ValueError
    and the recovery falls into still_failing.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nbody content recoverable via re-fetch.\n", encoding="utf-8")

    db = sqlite3.connect(":memory:")
    create_schema(db)

    # Seed a streaming-mode bronze row + dead_letter row
    StreamingBronzeStore(db).write("obsidian", "alpha.md", note.read_bytes(), "text/markdown")
    DeadLetterStore(db).record("obsidian", "alpha.md", "first-pass failed; should recover via re-fetch")
    db.commit()

    mapping = _streaming_obsidian_mapping(vault)
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=tmp_path / "bronze",
        config_mapping=mapping,
    )
    assert isinstance(result, ReextractResult)
    assert result.recovered == 1, f"streaming-mode recovery via re-fetch should succeed; got {result}"
    assert result.still_failing == 0
    assert result.skipped_source_unavailable == 0

    # dead_letter row cleared
    remaining = db.execute("SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?", ("obsidian",)).fetchone()[
        0
    ]
    assert remaining == 0

    db.close()


def test_reextract_streaming_row_with_source_deleted_routes_to_skipped(tmp_path: Path) -> None:
    """When the source file no longer exists, connector.fetch raises;
    the re-extract path counts it as skipped_source_unavailable (a
    streaming-specific recovery outcome).

    Sabotage proof: change the except branch to ``still_failing += 1``
    instead of ``skipped_source_unavailable += 1``; the test fails
    because the assertion on the new counter is zero.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "deleted.md"
    note.write_text("# Will be deleted\n\nbody.\n", encoding="utf-8")

    db = sqlite3.connect(":memory:")
    create_schema(db)

    StreamingBronzeStore(db).write("obsidian", "deleted.md", note.read_bytes(), "text/markdown")
    DeadLetterStore(db).record("obsidian", "deleted.md", "first-pass failed")
    db.commit()

    # Delete the source file so the connector can't re-fetch it
    note.unlink()

    mapping = _streaming_obsidian_mapping(vault)
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=tmp_path / "bronze",
        config_mapping=mapping,
    )
    assert result.recovered == 0
    assert result.skipped_source_unavailable == 1, (
        f"source file deletion should route to skipped_source_unavailable; got {result}"
    )

    # dead_letter row preserved for operator triage
    remaining = db.execute("SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?", ("obsidian",)).fetchone()[
        0
    ]
    assert remaining == 1

    db.close()


def test_reextract_mixed_streaming_and_filesystem_rows(tmp_path: Path) -> None:
    """Mixed pre-state: one streaming-mode row + one filesystem-mode row
    + one dead_letter row each. Re-extract handles both correctly through
    the dual-mode routing in _reextract_rows.

    Sabotage proof: invert the branching condition (``if ref.raw_path is not None``);
    both items fail because the wrong code path runs for each.
    """
    from kairix.core.connectors import StreamingBronzeStore

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "streaming-note.md").write_text("# Streaming Note\n\nbody.\n", encoding="utf-8")
    (vault / "filesystem-note.md").write_text("# Filesystem Note\n\nbody.\n", encoding="utf-8")

    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    # streaming-note: written by StreamingBronzeStore (raw_path = "" in DB)
    StreamingBronzeStore(db).write(
        "obsidian", "streaming-note.md", (vault / "streaming-note.md").read_bytes(), "text/markdown"
    )
    # filesystem-note: written by FilesystemBronzeStore (raw_path = real path)
    StreamingBronzeStore(db).write(
        "obsidian",
        "filesystem-note.md",
        (vault / "filesystem-note.md").read_bytes(),
        "text/markdown",
    )
    DeadLetterStore(db).record("obsidian", "streaming-note.md", "first-pass failed")
    DeadLetterStore(db).record("obsidian", "filesystem-note.md", "first-pass failed")
    db.commit()

    mapping = _streaming_obsidian_mapping(vault)
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_mapping=mapping,
    )
    assert result.recovered == 2, f"both mixed-mode rows should recover; got {result}"
    db.close()
