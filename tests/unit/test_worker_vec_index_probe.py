"""Unit tests for ``kairix.worker.probe_vec_index_at_boot``.

The probe runs at worker boot to surface vec_index recovery actions
(orphan .tmp promotion, corrupt-file recreation) immediately rather
than 6+ hours into the first embed run. This was the missing piece
when the 2026-05-31 production bug went undetected for hours.

The probe MUST:
  - Be a no-op when ``enabled=False``
  - Fix a corrupt vectors.usearch in place at boot
  - Promote an orphan .tmp from a previous crashed run
  - Never raise — log any failure as WARNING and let boot continue

F2-clean: tests pass ``db_path`` + ``enabled`` directly via the probe's
kwarg seam; no process env mutation.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.worker import probe_vec_index_at_boot

pytestmark = pytest.mark.unit


def _seed_db(path: Path) -> None:
    db = sqlite3.connect(str(path))
    create_schema(db)
    db.commit()
    db.close()


def _write_corrupt_index(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"NOT A USEARCH INDEX" + b"\x00" * 256)


def test_probe_recreates_corrupt_index_at_boot(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A corrupt vectors.usearch is auto-recovered at boot before any embed tick."""
    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    _seed_db(db_path)
    _write_corrupt_index(index_path)
    corrupt_bytes = index_path.read_bytes()

    with caplog.at_level(logging.WARNING, logger="kairix.core.search.vec_index"):
        probe_vec_index_at_boot(db_path=db_path, enabled=True)

    # Corrupt file deleted (load_or_recreate deletes on corruption).
    # Either the file is gone (fresh empty state) or it's been replaced
    # with the empty-index header — both are post-recovery shapes.
    assert not index_path.exists() or index_path.read_bytes() != corrupt_bytes, (
        "probe failed to clear the corrupt index"
    )
    # Recovery surfaced an actionable warning.
    messages = [r.getMessage() for r in caplog.records]
    assert any("corrupt" in m.lower() or "recreating" in m.lower() for m in messages), (
        f"expected recovery WARNING from the probe; got {messages}"
    )


def test_probe_is_no_op_when_disabled(tmp_path: Path) -> None:
    """When the operator opted out of worker vec writes, the probe stays out of the way."""
    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    _seed_db(db_path)
    _write_corrupt_index(index_path)
    corrupt_bytes = index_path.read_bytes()

    probe_vec_index_at_boot(db_path=db_path, enabled=False)

    # Corrupt file untouched — the probe didn't fire.
    assert index_path.read_bytes() == corrupt_bytes, "probe ran despite enabled=False"


def test_probe_never_raises_even_on_unexpected_failure(tmp_path: Path) -> None:
    """If anything inside the probe raises, boot must continue — never crashloop."""
    # Point at a non-existent DB dir so the inner path resolution fails
    # in an unexpected way (parent of vectors.usearch isn't writable).
    bogus_db = Path("/proc/this/does/not/exist/index.sqlite")

    # Must not raise — even when the inner operation can't possibly succeed.
    probe_vec_index_at_boot(db_path=bogus_db, enabled=True)
