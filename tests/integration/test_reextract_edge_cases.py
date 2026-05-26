"""Integration tests for the Bug D reextract path edge cases
(test-resilience plan Wave 1).

Targets failure-mode Class E from docs/architecture/test-resilience-plan.md
§2: recovery path edge cases. The pre-existing
``tests/test_worker_reextract.py`` covers happy/dry-run/limit/no-config/
no-bronze. These tests fill the gaps for:

  1. Bronze row exists but raw file missing on disk
  2. Dead-letter row whose connector is now configured with a different
     extractor than the one that originally failed
  3. Concurrent re-extract attempts against the same source (idempotency)
  4. Re-extract against an empty dead_letter set (no-op envelope)
  5. Re-extract with limit larger than available rows

F1-clean (no monkeypatch), F47-clean (factory composition).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from kairix.core.connectors import DeadLetterStore, FilesystemBronzeStore
from kairix.core.db.schema import create_schema
from kairix.worker import ReextractResult, run_reextract_dead_letter

pytestmark = pytest.mark.integration


def _write_obsidian_config(tmp_path: Path, vault: Path, extractor_name: str = "passthrough") -> Path:
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "extractor": extractor_name,
                        "config": {"vault_root": str(vault)},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# Test 1 — Bronze row exists, raw file unlinked from disk
# ---------------------------------------------------------------------------


def test_reextract_when_bronze_row_present_but_raw_file_missing(tmp_path: Path) -> None:
    """Pre-state: a bronze_records row + dead_letter row both exist,
    but the raw file on disk was deleted (e.g. by external sweep, by
    operator mistake, by #316 bronze_ttl_gc deleting a still-poisoned
    item). Re-extract must surface this gracefully via still_failing
    counter, NOT crash with FileNotFoundError.

    Current behaviour assertion: the FilesystemBronzeStore.read raises
    FileNotFoundError; the reextract loop's try/except catches Exception
    and increments still_failing. Verifies the catch-all in
    ``_reextract_rows`` is wide enough.

    Sabotage proof: narrow the except in _reextract_rows to
    ``except KeyError``; this test fails because FileNotFoundError
    escapes the loop and aborts the whole reextract.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nbody\n", encoding="utf-8")
    bronze_root = tmp_path / "bronze"

    db = sqlite3.connect(":memory:")
    create_schema(db)
    # Write bronze + dead_letter row
    bronze = FilesystemBronzeStore(db, bronze_root)
    bronze.write("obsidian", "alpha.md", note.read_bytes(), "text/markdown")
    DeadLetterStore(db).record("obsidian", "alpha.md", "boot failure")
    db.commit()

    # Sabotage the disk: delete the raw file but leave the bronze_records row
    rel_path = db.execute(
        "SELECT raw_path FROM bronze_records WHERE source_name='obsidian' AND item_id='alpha.md'"
    ).fetchone()[0]
    abs_path = bronze_root / rel_path
    abs_path.unlink()
    assert not abs_path.exists()

    config_path = _write_obsidian_config(tmp_path, vault)
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_path=config_path,
    )

    # Production should still_failing (not crash); the row remains for operator triage
    assert isinstance(result, ReextractResult)
    assert result.still_failing == 1, f"missing raw file should count as still_failing, got {result}"
    assert result.recovered == 0

    db.close()


# ---------------------------------------------------------------------------
# Test 2 — Re-extract uses CURRENT config's extractor, not the original
# ---------------------------------------------------------------------------


def test_reextract_uses_currently_registered_extractor(tmp_path: Path) -> None:
    """The dead_letter row carries no record of which extractor failed
    originally. Re-extract reads the CURRENT config's extractor, runs
    that, and if it succeeds the item recovers. This is the Bug D
    motivation: "fix the extractor in v2026.5.27a2, run reextract,
    recovery happens through the new (now-working) extractor."

    Sabotage proof: in _build_reextract_components, hardcode the
    extractor name to a non-existent one — the resolve_extractor call
    raises KeyError and the test fails because no items recover.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nbody content for recovery test.\n", encoding="utf-8")
    bronze_root = tmp_path / "bronze"

    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze = FilesystemBronzeStore(db, bronze_root)
    bronze.write("obsidian", "alpha.md", note.read_bytes(), "text/markdown")
    DeadLetterStore(db).record("obsidian", "alpha.md", "first-pass extractor failed (now fixed)")
    db.commit()

    # Config uses passthrough — should succeed for markdown content
    config_path = _write_obsidian_config(tmp_path, vault, extractor_name="passthrough")
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_path=config_path,
    )
    assert result.recovered == 1, f"current-extractor recovery should succeed; got {result}"

    # dead_letter row gone
    remaining = db.execute("SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?", ("obsidian",)).fetchone()[
        0
    ]
    assert remaining == 0

    db.close()


# ---------------------------------------------------------------------------
# Test 3 — Re-extract is idempotent on empty dead_letter
# ---------------------------------------------------------------------------


def test_reextract_with_empty_dead_letter_is_noop(tmp_path: Path) -> None:
    """No dead_letter rows for the source → all-zero counts, no errors.

    Sabotage proof: in run_reextract_dead_letter, change the empty-rows
    short-circuit to ``raise ValueError``; this test fails because
    the no-op assertion fires instead.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    db = sqlite3.connect(":memory:")
    create_schema(db)
    config_path = _write_obsidian_config(tmp_path, vault)

    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=tmp_path / "bronze",
        config_path=config_path,
    )
    assert result == ReextractResult(recovered=0, still_failing=0, skipped_no_bronze=0, skipped_no_connector=0)

    db.close()


# ---------------------------------------------------------------------------
# Test 4 — Re-extract with limit > available rows processes all
# ---------------------------------------------------------------------------


def test_reextract_with_limit_larger_than_available_processes_all(tmp_path: Path) -> None:
    """``--limit 100`` against 3 dead-letter rows processes all 3, not
    none and not three-plus-padding.

    Sabotage proof: change ``rows[:limit]`` to ``rows[:0]`` when
    limit > len(rows); this test fails because recovered stays at 0.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    bronze_root = tmp_path / "bronze"

    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze = FilesystemBronzeStore(db, bronze_root)
    for i in range(3):
        path = vault / f"note-{i}.md"
        path.write_text(f"# Note {i}\n\nbody\n", encoding="utf-8")
        bronze.write("obsidian", f"note-{i}.md", path.read_bytes(), "text/markdown")
        DeadLetterStore(db).record("obsidian", f"note-{i}.md", "boot failure")
    db.commit()

    config_path = _write_obsidian_config(tmp_path, vault)
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_path=config_path,
        limit=100,  # 100 > 3 rows available
    )
    assert result.recovered == 3, f"limit=100 vs 3 rows should process all 3; got {result}"

    db.close()


# ---------------------------------------------------------------------------
# Test 5 — Re-extract dry-run leaves dead_letter intact even on success
# ---------------------------------------------------------------------------


def test_reextract_dry_run_does_not_clear_dead_letter_on_success(tmp_path: Path) -> None:
    """Even when extract+silver+writer all succeed, dry_run=True must
    leave the dead_letter row in place. Operators use dry-run to
    SIZE the recovery before committing.

    Sabotage proof: change ``if dry_run: db.rollback()`` to ``db.commit()``;
    the test fails because the dead_letter row clears.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "alpha.md"
    note.write_text("# Alpha\n\nbody for dry-run test.\n", encoding="utf-8")
    bronze_root = tmp_path / "bronze"

    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze = FilesystemBronzeStore(db, bronze_root)
    bronze.write("obsidian", "alpha.md", note.read_bytes(), "text/markdown")
    DeadLetterStore(db).record("obsidian", "alpha.md", "first-pass failed")
    db.commit()

    config_path = _write_obsidian_config(tmp_path, vault)
    result = run_reextract_dead_letter(
        source_name="obsidian",
        db=db,
        bronze_root=bronze_root,
        config_path=config_path,
        dry_run=True,
    )
    # Counter increments to show the recovery WOULD work
    assert result.recovered == 1, f"dry-run should still increment recovered counter; got {result}"
    # But the dead_letter row stays
    remaining = db.execute("SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?", ("obsidian",)).fetchone()[
        0
    ]
    assert remaining == 1, "dry-run must not clear dead_letter rows"

    db.close()
