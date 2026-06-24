"""F30 outcome test — ``kairix dead-letter drain`` subprocess surface (PR-5).

Per F30: every CLI subcommand in ``kairix/cli.py:COMMANDS`` ships with an
outcome test that (a) invokes via
``subprocess.run([python, -m, kairix.cli, dead-letter, drain, ...])`` with
a ``--db-path`` flag (no ``KAIRIX_*`` env vars per F2), and (b) asserts on
``.stdout`` / ``.stderr`` content (not on returncode alone).

The drain verb closes the orphaned-source gap: dead-letters from a source
whose connector is no longer active never drain through the per-connector
auto-drain. This file covers the operator one-shot:

* named single-source drain clears that ORPHANED source's backlog;
* no-source drain sweeps EVERY distinct source;
* ``--dry-run`` reports what WOULD drain WITHOUT mutating.

Sabotage proof executed before commit (mutate → run → fail → restore →
pass): the ``--dry-run`` test is the load-bearing one — making
``_run_drain`` ignore the ``dry_run`` flag clears the row and the
``COUNT(*) == 1`` post-condition fails.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.integration

# A permanently-unprocessable last_error + a known-unsupported MIME so the
# drain core's narrow eligibility (corrupt_zip OR known-unsupported MIME)
# fires for the seeded rows.
_ERR_DRAINABLE = "some other connector error"
_MIME_UNSUPPORTED = "application/msword"


def _bootstrap_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.commit()
    db.close()
    return db_path


def _seed_orphan(db_path: Path, source_name: str) -> None:
    """One drainable dead-letter + its unsupported-MIME bronze row."""
    db = sqlite3.connect(str(db_path))
    try:
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_name, "poison-1", 3, _ERR_DRAINABLE, "2026-05-26T05:58:00Z"),
        )
        db.execute(
            "INSERT INTO bronze_records "
            "(source_name, item_id, raw_path, mime, fetched_at, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_name, "poison-1", "/bronze/p1", _MIME_UNSUPPORTED, "2026-05-26T05:50:00Z", f"h-{source_name}"),
        )
        db.commit()
    finally:
        db.close()


def _remaining(db_path: Path, source_name: str) -> int:
    db = sqlite3.connect(str(db_path))
    try:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
                (source_name,),
            ).fetchone()[0]
        )
    finally:
        db.close()


def _run_drain(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", "dead-letter", "drain", "--db-path", str(db_path), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_drain_named_orphaned_source_clears_backlog(tmp_path: Path) -> None:
    """``dead-letter drain SOURCE`` clears an ORPHANED source's drainable row."""
    db_path = _bootstrap_db(tmp_path)
    _seed_orphan(db_path, "removed-sharepoint")
    proc = _run_drain(db_path, "removed-sharepoint")
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "removed-sharepoint" in proc.stdout
    assert "drained 1" in proc.stdout, f"expected 'drained 1'; got: {proc.stdout!r}"
    assert _remaining(db_path, "removed-sharepoint") == 0


def test_drain_all_sources_sweeps_every_source(tmp_path: Path) -> None:
    """``dead-letter drain`` with no source sweeps every distinct source."""
    db_path = _bootstrap_db(tmp_path)
    _seed_orphan(db_path, "removed-sharepoint")
    _seed_orphan(db_path, "removed-gdrive")
    proc = _run_drain(db_path)
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "across 2 source(s)" in proc.stdout, f"expected two-source total; got: {proc.stdout!r}"
    assert _remaining(db_path, "removed-sharepoint") == 0
    assert _remaining(db_path, "removed-gdrive") == 0


def test_drain_dry_run_mutates_nothing(tmp_path: Path) -> None:
    """``dead-letter drain SOURCE --dry-run`` reports but leaves the row.

    Sabotage: make ``_run_drain`` ignore ``dry_run`` → the row clears and
    the ``_remaining == 1`` post-condition fails.
    """
    db_path = _bootstrap_db(tmp_path)
    _seed_orphan(db_path, "removed-sharepoint")
    proc = _run_drain(db_path, "removed-sharepoint", "--dry-run")
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "dry-run" in proc.stdout, f"expected dry-run marker; got: {proc.stdout!r}"
    assert "would drain 1" in proc.stdout
    # Nothing was mutated — the row survives.
    assert _remaining(db_path, "removed-sharepoint") == 1
