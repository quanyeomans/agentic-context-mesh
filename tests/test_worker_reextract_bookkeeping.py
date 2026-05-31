"""Tests for GH #351 — reextract failure branch updates dead-letter bookkeeping.

Pre-fix: ``_reextract_one``'s ``except Exception`` branch rolled back the
per-item transaction but never bumped ``connector_deadletter.failure_count``
/ ``last_attempt`` / ``last_error``. Operators saw stale error text after
a fresh reextract attempt; the poisoning threshold (fc >= 3) never
advanced for items routed through reextract; diagnosis was harder than
needed because the new exception class (e.g. ``BadZipFile`` instead of
the original ``MissingDependency``) never surfaced.

Post-fix: every failed extract bumps ``failure_count`` by 1, sets
``last_attempt`` to ``now()``, and writes ``last_error`` containing
``"reextract: <exception>"``. ``dry_run=True`` preserves the
"commits nothing" contract — the row stays untouched.

Both tests drive the public ``run_reextract_dead_letter`` API.
``pdf_fallback`` is configured as the extractor; the bronze record
carries ``application/pdf`` + bytes that are NOT a valid PDF, so
``pdfplumber.open`` raises and the still-failing branch executes.
This is the natural-failure path through the production code; no
monkey-patching, no internal-symbol imports.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from kairix.core.connectors import StreamingBronzeStore
from kairix.core.db.schema import create_schema
from kairix.worker import ReextractResult, run_reextract_dead_letter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


# Bytes that fail PDF validation in pdfplumber — written into the vault so
# the obsidian connector can re-fetch them at reextract time and the
# pdf_fallback extractor raises ``PdfminerException`` on parse.
_BAD_PDF_BYTES = b"NOT A REAL PDF, ONLY A LURE FOR THE EXTRACTOR"

# Stable pre-state values seeded into the dead-letter row so the
# assertions on "row changed" / "row preserved" carry concrete deltas.
_OLD_FAILURE_COUNT = 1
_OLD_ERROR = "stale error from boot"
_OLD_ATTEMPT_ISO = "2020-01-01T00:00:00+00:00"


def _write_pdf_fallback_config(tmp_path: Path, vault: Path) -> Path:
    """Write a kairix.config.yaml routing obsidian through pdf_fallback.

    pdf_fallback is the extractor that will raise on invalid PDF bytes.
    """
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "connectors": [
                    {
                        "name": "obsidian",
                        "extractor": "pdf_fallback",
                        "config": {"vault_root": str(vault)},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _seed_dead_letter_with_history(
    *,
    db: sqlite3.Connection,
    source_name: str,
    item_id: str,
    failure_count: int = _OLD_FAILURE_COUNT,
    error: str = _OLD_ERROR,
    attempt_iso: str = _OLD_ATTEMPT_ISO,
) -> None:
    """Insert a dead_letter row with explicit pre-state values.

    Direct INSERT (not ``DeadLetterStore.record``) so the test pins
    pre-state ``failure_count`` / ``last_error`` / ``last_attempt`` to
    known values; ``record()`` would stamp ``last_attempt = now()`` and
    defeat the "row contains stale state" framing.
    """
    db.execute(
        "INSERT INTO connector_deadletter "
        "(source_name, item_id, failure_count, last_error, last_attempt) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_name, item_id, failure_count, error, attempt_iso),
    )
    db.commit()


def _fetch_dead_letter_row(db: sqlite3.Connection, source_name: str, item_id: str) -> tuple[int, str, str]:
    """Return ``(failure_count, last_error, last_attempt)`` for the row."""
    row = db.execute(
        "SELECT failure_count, last_error, last_attempt "
        "FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
        (source_name, item_id),
    ).fetchone()
    assert row is not None, f"dead-letter row missing for ({source_name!r}, {item_id!r})"
    return int(row[0]), str(row[1]), str(row[2])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reextract_failure_bumps_failure_count_and_records_fresh_error(tmp_path: Path) -> None:
    """GH #351 — when extract raises, the dead-letter row's bookkeeping
    columns update: ``failure_count`` increments, ``last_error`` carries
    the fresh exception string, ``last_attempt`` advances to ``now()``.

    Drives the public ``run_reextract_dead_letter``; pdf_fallback is
    configured so the broken-PDF bytes trigger pdfplumber's
    ``PdfminerException`` deep inside the extract step.

    Sabotage proof (executed): commented out the ``dead_letter.record(...)``
    call in ``_reextract_one``'s except branch and re-ran. The test
    failed with ``failure_count == 1`` (stale) instead of the expected
    ``2``, and ``last_error == 'stale error from boot'``. Restored, it
    passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    bad_pdf = vault / "broken.pdf"
    bad_pdf.write_bytes(_BAD_PDF_BYTES)
    source_name = "obsidian"
    item_id = "broken.pdf"

    db = sqlite3.connect(":memory:")
    create_schema(db)
    StreamingBronzeStore(db).write(source_name, item_id, _BAD_PDF_BYTES, "application/pdf")
    _seed_dead_letter_with_history(db=db, source_name=source_name, item_id=item_id)

    config_path = _write_pdf_fallback_config(tmp_path, vault)

    before_ts = datetime.now(timezone.utc)
    # Sleep a tick so timestamp ordering is unambiguous on fast CI runners.
    time.sleep(0.01)

    result = run_reextract_dead_letter(
        source_name=source_name,
        db=db,
        bronze_root=tmp_path / "bronze",
        config_path=config_path,
    )

    assert isinstance(result, ReextractResult)
    assert result.still_failing == 1, f"broken-PDF reextract should land in still_failing; got {result}"
    assert result.recovered == 0

    failure_count, last_error, last_attempt = _fetch_dead_letter_row(db, source_name, item_id)

    assert failure_count == 2, (
        f"failure_count must increment {_OLD_FAILURE_COUNT} -> 2 on each retry; "
        f"got {failure_count}. fix: ensure _reextract_one's except branch "
        f"calls dead_letter.record(...)."
    )
    assert last_error != _OLD_ERROR, (
        f"last_error must refresh to the new failure text, not stay at the "
        f"seeded stale value {_OLD_ERROR!r}; got {last_error!r}."
    )
    assert "reextract:" in last_error, (
        f"last_error should be prefixed with 'reextract:' so triage knows "
        f"the failure happened on a reextract attempt (not a sync attempt). "
        f"got {last_error!r}."
    )

    # last_attempt must have advanced from the seeded 2020 value to now().
    attempt_dt = datetime.fromisoformat(last_attempt)
    assert attempt_dt >= before_ts, (
        f"last_attempt must advance to recent timestamp; got {last_attempt!r} "
        f"(before_ts={before_ts.isoformat()}, seeded was {_OLD_ATTEMPT_ISO!r})."
    )

    db.close()


def test_reextract_dry_run_failure_does_not_touch_bookkeeping(tmp_path: Path) -> None:
    """``dry_run=True`` honours the "commits nothing" contract even on
    the failure branch — the dead-letter row stays untouched so
    operators can pre-flight a recovery without dirtying the table.

    Sabotage proof (executed): removed the ``if not dry_run:`` guard in
    ``_reextract_one``'s except branch and re-ran. The test failed
    because ``failure_count`` advanced to 2 and ``last_error`` changed
    even though the run was a dry-run. Restored, it passes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    bad_pdf = vault / "broken.pdf"
    bad_pdf.write_bytes(_BAD_PDF_BYTES)
    source_name = "obsidian"
    item_id = "broken.pdf"

    db = sqlite3.connect(":memory:")
    create_schema(db)
    StreamingBronzeStore(db).write(source_name, item_id, _BAD_PDF_BYTES, "application/pdf")
    _seed_dead_letter_with_history(db=db, source_name=source_name, item_id=item_id)

    config_path = _write_pdf_fallback_config(tmp_path, vault)

    result = run_reextract_dead_letter(
        source_name=source_name,
        db=db,
        bronze_root=tmp_path / "bronze",
        config_path=config_path,
        dry_run=True,
    )

    assert result.still_failing == 1, f"dry-run on failing PDF should still count still_failing; got {result}"

    failure_count, last_error, last_attempt = _fetch_dead_letter_row(db, source_name, item_id)

    assert failure_count == _OLD_FAILURE_COUNT, (
        f"dry_run must NOT bump failure_count; got {failure_count}, expected "
        f"{_OLD_FAILURE_COUNT}. fix: gate the dead_letter.record(...) call on "
        f"`if not dry_run:`."
    )
    assert last_error == _OLD_ERROR, (
        f"dry_run must NOT overwrite last_error; got {last_error!r}, expected "
        f"{_OLD_ERROR!r}. fix: gate the dead_letter.record(...) call on "
        f"`if not dry_run:`."
    )
    assert last_attempt == _OLD_ATTEMPT_ISO, (
        f"dry_run must NOT advance last_attempt; got {last_attempt!r}, expected "
        f"{_OLD_ATTEMPT_ISO!r}. fix: gate the dead_letter.record(...) call on "
        f"`if not dry_run:`."
    )

    db.close()
