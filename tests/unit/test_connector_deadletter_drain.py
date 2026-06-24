"""Unit + contract tests for PR-4 dead-letter auto-drain.

Covers :mod:`kairix.core.connectors.deadletter_drain`:

* :func:`is_drain_eligible` — the two and only drainable shapes
  (failure_class == corrupt_zip, OR a KNOWN_UNSUPPORTED MIME under any
  class) AND every NON-drainable shape (timeout / 403 / 404 / 429 /
  no_space / missing_dependency / other; decode on ANY MIME — including
  the SUPPORTED pdf/docx/pptx/xlsx/png that are RECOVERABLE operator
  state; unknown-or-missing MIME / octet-stream / text) proving the
  conservative "when in doubt, do NOT drain" contract;
* :func:`drain_connector_deadletters` — eligible rows are cleared +
  recorded ``skipped_unsupported``; non-eligible rows are LEFT in the
  queue; the pass is idempotent (a second run is a no-op); a ``clear``
  error on one row logs + continues without aborting the rest; the
  summary tally + cheap-when-clean guard.

The drain is exercised against the REAL ``connector_deadletter`` /
``bronze_records`` / ``documents_media`` tables via ``create_schema`` and
the REAL :class:`DefaultSilverProcessor` + :class:`SqliteDocumentsMediaWriter`,
so the documents_media outcome row is asserted end-to-end.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.connectors.deadletter_drain import (
    DrainSummary,
    drain_connector_deadletters,
    is_drain_eligible,
)
from kairix.core.connectors.silver import DefaultSilverProcessor, SqliteDocumentsMediaWriter
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit

_CONNECTOR = "sharepoint"

# Canonical error strings that classify_error buckets deterministically.
_ERR_CORRUPT_ZIP = "zipfile.BadZipFile: File is not a zip file"
_ERR_DECODE = "'utf-8' codec can't decode byte 0x80 in position 0"
_ERR_TIMEOUT = "Request timed out after 30s"
_ERR_403 = "HTTP 403 Forbidden"
_ERR_404 = "HTTP 404 Not Found"
_ERR_429 = "429 Too Many Requests"
_ERR_NO_SPACE = "No space left on device"
_ERR_MISSING_DEP = "MissingDependencyException: install libreoffice"
_ERR_OTHER = "some other weird connector error"

_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_PDF = "application/pdf"
_MIME_MSWORD = "application/msword"
_MIME_VISIO = "application/vnd.ms-visio.drawing"
_MIME_PNG = "image/png"
_MIME_TEXT = "text/plain"
_MIME_OCTET = "application/octet-stream"
_STATUS_SKIPPED = "skipped_unsupported"


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "kairix.sqlite"))
    create_schema(db)
    return db


def _seed_dead_letter(
    db: sqlite3.Connection,
    item_id: str,
    last_error: str,
    *,
    failure_count: int = 3,
    source_name: str = _CONNECTOR,
) -> None:
    db.execute(
        "INSERT INTO connector_deadletter "
        "(source_name, item_id, failure_count, last_error, last_attempt) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_name, item_id, failure_count, last_error, "2026-06-20T00:00:00Z"),
    )


def _seed_bronze(
    db: sqlite3.Connection,
    item_id: str,
    mime: str,
    *,
    content_hash: str | None = "hash-x",
    source_name: str = _CONNECTOR,
) -> None:
    db.execute(
        "INSERT INTO bronze_records "
        "(source_name, item_id, raw_path, mime, fetched_at, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_name, item_id, f"/bronze/{item_id}", mime, "2026-06-19T00:00:00Z", content_hash),
    )


def _silver(db: sqlite3.Connection) -> DefaultSilverProcessor:
    return DefaultSilverProcessor(documents_media_writer=SqliteDocumentsMediaWriter(db))


def _remaining_item_ids(db: sqlite3.Connection) -> set[str]:
    return {e.item_id for e in DeadLetterStore(db).list(_CONNECTOR)}


def _documents_media_status(db: sqlite3.Connection, content_hash: str) -> str | None:
    row = db.execute(
        "SELECT extraction_status FROM documents_media WHERE hash = ?",
        (content_hash,),
    ).fetchone()
    return None if row is None else str(row[0])


# --------------------------------------------------------------------------
# is_drain_eligible — eligibility matrix (every branch)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mime", "klass"),
    [
        (_MIME_TEXT, "corrupt_zip"),  # corrupt_zip drains regardless of MIME
        (_MIME_DOCX, "corrupt_zip"),
        (None, "corrupt_zip"),
        (_MIME_PNG, "corrupt_zip"),  # even a supported binary MIME drains under corrupt_zip
        (_MIME_OCTET, "corrupt_zip"),
        (_MIME_MSWORD, "other"),  # KNOWN_UNSUPPORTED MIME drains on its own
        (_MIME_VISIO, "timeout"),  # prefix-family unsupported MIME, even with a transient class
    ],
)
def test_eligible_rows_are_drainable(mime: str | None, klass: str) -> None:
    """Each permanently-unprocessable shape returns True.

    Drain IFF (failure_class == corrupt_zip) OR (mime is KNOWN_UNSUPPORTED).

    Sabotage: drop the ``corrupt_zip`` clause in is_drain_eligible →
    the corrupt_zip cases fail; drop the known_unsupported_mime clause →
    the MSWORD / Visio cases fail.
    """
    assert is_drain_eligible(mime, klass) is True


@pytest.mark.parametrize(
    ("mime", "klass"),
    [
        (_MIME_DOCX, "other"),  # supported MIME, non-permanent class → leave
        (_MIME_TEXT, "timeout"),
        (_MIME_TEXT, "decode"),  # decode on text = recoverable codepage → leave
        (_MIME_OCTET, "decode"),  # octet-stream is UNKNOWN (mislabeled-OOXML) → leave
        (_MIME_OCTET, "other"),
        (None, "decode"),  # missing bronze MIME → leave
        (None, "timeout"),
        (_MIME_PNG, "timeout"),  # transient class on a binary MIME → leave
        (_MIME_PNG, "forbidden_403"),
        (_MIME_PNG, "not_found_404"),  # NIT-4: more transient-on-binary cases
        (_MIME_PNG, "rate_limit"),
        (_MIME_PNG, "no_space"),
        (_MIME_DOCX, "missing_dependency"),  # recoverable once the lib lands → leave
        # CRITICAL-1: a `decode` failure on a SUPPORTED format is RECOVERABLE
        # operator state (re-runnable via `kairix worker reextract`) and must
        # NEVER be drained — the old `decode`+binary-MIME branch would have
        # permanently destroyed these.
        (_MIME_PDF, "decode"),
        (_MIME_DOCX, "decode"),
        (_MIME_PPTX, "decode"),
        (_MIME_XLSX, "decode"),
        (_MIME_PNG, "decode"),  # decode on a concrete binary image → leave
    ],
)
def test_non_eligible_rows_are_left(mime: str | None, klass: str) -> None:
    """Transient / recoverable / unknown-MIME / decode-on-supported shapes
    return False.

    Drain IFF (failure_class == corrupt_zip) OR (mime is KNOWN_UNSUPPORTED);
    none of these rows match either clause.

    Sabotage: re-introduce the removed ``decode``-on-binary branch → the
    PDF/DOCX/PPTX/XLSX/PNG decode cases drain and these fail; make
    is_drain_eligible ``return True`` unconditionally → every one fails.
    """
    assert is_drain_eligible(mime, klass) is False


def test_unsupported_mime_drains_even_under_transient_class() -> None:
    """A KNOWN_UNSUPPORTED MIME is permanent regardless of the failure
    class — no extractor will ever claim it, so even a 'timeout' /
    'missing_dependency' row on an unsupported MIME drains.

    Sabotage: drop the known_unsupported_mime clause → these become False.
    """
    assert is_drain_eligible(_MIME_MSWORD, "timeout") is True
    assert is_drain_eligible(_MIME_MSWORD, "missing_dependency") is True
    assert is_drain_eligible(_MIME_VISIO, "other") is True


@pytest.mark.parametrize(
    "mime",
    [_MIME_TEXT, _MIME_DOCX, _MIME_PDF, _MIME_PNG, _MIME_OCTET, None],
)
def test_corrupt_zip_drains_under_any_mime(mime: str | None) -> None:
    """corrupt_zip is a malformed archive — re-fetch can never repair it —
    so it drains regardless of the bronze MIME (even supported / missing).

    Sabotage: drop the corrupt_zip clause → every one of these becomes False.
    """
    assert is_drain_eligible(mime, "corrupt_zip") is True


@pytest.mark.parametrize(
    "klass",
    ["timeout", "forbidden_403", "not_found_404", "rate_limit", "no_space", "missing_dependency", "other"],
)
def test_transient_classes_with_unknown_mime_never_drain(klass: str) -> None:
    """Every retryable class on a missing/unknown MIME is left for retry.

    Sabotage: make is_drain_eligible ``return True`` unconditionally →
    every one of these fails.
    """
    assert is_drain_eligible(None, klass) is False
    assert is_drain_eligible(_MIME_OCTET, klass) is False


# --------------------------------------------------------------------------
# drain_connector_deadletters — end-to-end against real tables
# --------------------------------------------------------------------------


def test_drain_clears_eligible_and_records_outcome(tmp_path: Path) -> None:
    """corrupt_zip + unsupported-MIME rows are cleared and each writes a
    ``skipped_unsupported`` documents_media row.

    The ``png-item`` row is a ``decode`` failure on a SUPPORTED image MIME:
    it is RECOVERABLE and must be LEFT in the queue (CRITICAL-1), so it
    keeps the post-drain ``left`` at 1 and stays in the remaining set.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, "zip-item", _ERR_CORRUPT_ZIP)
        _seed_bronze(db, "zip-item", _MIME_DOCX, content_hash="hash-zip")
        _seed_dead_letter(db, "doc-item", _ERR_OTHER)
        _seed_bronze(db, "doc-item", _MIME_MSWORD, content_hash="hash-doc")
        # decode-on-supported-binary: RECOVERABLE — must NOT be drained.
        _seed_dead_letter(db, "png-item", _ERR_DECODE)
        _seed_bronze(db, "png-item", _MIME_PNG, content_hash="hash-png")
        db.commit()

        summary = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))

        assert summary.drained == 2
        assert summary.corrupt_zip == 1
        assert summary.unsupported_mime == 1
        assert summary.left == 1  # the recoverable png-item is left behind
        assert _remaining_item_ids(db) == {"png-item"}
        for h in ("hash-zip", "hash-doc"):
            assert _documents_media_status(db, h) == _STATUS_SKIPPED
        # the recoverable decode row wrote NO skipped_unsupported outcome.
        assert _documents_media_status(db, "hash-png") is None
    finally:
        db.close()


def test_left_reflects_true_depth_when_backlog_exceeds_scan_cap(tmp_path: Path) -> None:
    """``left`` is the TRUE post-drain queue depth, not ``scanned - drained``.

    Seeds a backlog DEEPER than the per-tick ``max_items`` scan cap, made
    of recoverable (left) rows so nothing drains. With ``max_items=2`` only
    2 of 5 rows are scanned; a naive ``len(candidates) - drained`` would
    report ``left == 2``. The real ``COUNT(*)`` must report the full 5.

    IMPORTANT-3 sabotage: revert ``left`` to ``len(candidates) - drained``
    → this asserts 2 and fails.
    """
    db = _open_db(tmp_path)
    try:
        for i in range(5):
            _seed_dead_letter(db, f"left-{i}", _ERR_TIMEOUT)
            _seed_bronze(db, f"left-{i}", _MIME_TEXT)
        db.commit()

        summary = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db), max_items=2)

        assert summary.drained == 0
        # True depth — every one of the 5 rows is still queued.
        assert summary.left == 5
        assert len(_remaining_item_ids(db)) == 5
    finally:
        db.close()


def test_left_reflects_true_depth_after_partial_drain_over_cap(tmp_path: Path) -> None:
    """With a mixed over-cap backlog, ``left`` counts EVERY surviving row.

    5 unsupported-MIME (drainable) rows, scan cap 2 → exactly 2 drain this
    tick; the real remaining count is 3 (the 3 unscanned rows), NOT
    ``scanned(2) - drained(2) == 0``.
    """
    db = _open_db(tmp_path)
    try:
        for i in range(5):
            _seed_dead_letter(db, f"u-{i}", _ERR_OTHER)
            _seed_bronze(db, f"u-{i}", _MIME_MSWORD, content_hash=f"h-{i}")
        db.commit()

        summary = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db), max_items=2)

        assert summary.drained == 2
        assert summary.left == 3  # 5 seeded - 2 drained; NOT scanned(2)-drained(2)
        assert len(_remaining_item_ids(db)) == 3
    finally:
        db.close()


def test_drain_leaves_transient_and_text_and_missing(tmp_path: Path) -> None:
    """timeout / 403 / 404 / missing_dependency / text-MIME / octet-stream
    and a bronze-less row are all LEFT in the queue.

    Sabotage: weaken is_drain_eligible to drain on any class → these rows
    vanish and the assertion on the remaining set fails.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, "to", _ERR_TIMEOUT)
        _seed_bronze(db, "to", _MIME_PNG)  # transient class on binary MIME → leave
        _seed_dead_letter(db, "f403", _ERR_403)
        _seed_bronze(db, "f403", _MIME_TEXT)
        _seed_dead_letter(db, "f404", _ERR_404)
        _seed_bronze(db, "f404", _MIME_TEXT)
        _seed_dead_letter(db, "f429", _ERR_429)
        _seed_bronze(db, "f429", _MIME_TEXT)
        _seed_dead_letter(db, "nospace", _ERR_NO_SPACE)
        _seed_bronze(db, "nospace", _MIME_PNG)
        _seed_dead_letter(db, "mdep", _ERR_MISSING_DEP)
        _seed_bronze(db, "mdep", _MIME_TEXT)
        _seed_dead_letter(db, "txt-decode", _ERR_DECODE)
        _seed_bronze(db, "txt-decode", _MIME_TEXT)
        _seed_dead_letter(db, "octet", _ERR_DECODE)
        _seed_bronze(db, "octet", _MIME_OCTET)
        _seed_dead_letter(db, "no-bronze", _ERR_DECODE)  # no bronze row at all
        db.commit()

        summary = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))

        # Nothing eligible — every row is transient / text / octet / bronze-less.
        assert summary.drained == 0
        assert _remaining_item_ids(db) == {
            "to",
            "f403",
            "f404",
            "f429",
            "nospace",
            "mdep",
            "txt-decode",
            "octet",
            "no-bronze",
        }
        assert summary.left == 9
    finally:
        db.close()


def test_drain_is_idempotent(tmp_path: Path) -> None:
    """A second drain pass over the same DB clears nothing more (no-op).

    Sabotage: make DeadLetterStore.clear non-idempotent (raise on miss) →
    the second pass raises instead of returning a zero-drained summary.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, "doc-item", _ERR_OTHER)
        _seed_bronze(db, "doc-item", _MIME_MSWORD, content_hash="hash-doc")
        db.commit()

        first = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))
        second = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))

        assert first.drained == 1
        assert second.drained == 0
        assert second == DrainSummary(_CONNECTOR, 0, 0, 0, 0)
        assert _remaining_item_ids(db) == set()
    finally:
        db.close()


def test_drain_without_content_hash_still_clears(tmp_path: Path) -> None:
    """An eligible row whose bronze has no content_hash is cleared (the
    outcome write is a silent no-op; clear is the load-bearing mutation).

    Sabotage: gate the clear() behind a non-None content_hash → this row
    survives and the remaining-set assertion fails.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, "doc-item", _ERR_OTHER)
        _seed_bronze(db, "doc-item", _MIME_MSWORD, content_hash=None)
        db.commit()

        summary = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))

        assert summary.drained == 1
        assert _remaining_item_ids(db) == set()
        # No documents_media row (no content_hash to key on) — but cleared.
        count = db.execute("SELECT COUNT(*) FROM documents_media").fetchone()[0]
        assert count == 0
    finally:
        db.close()


def test_drain_empty_queue_is_cheap_noop(tmp_path: Path) -> None:
    """No dead-letter rows → zero-summary, no work.

    Sabotage: remove the empty-candidates guard → still returns the same
    zero summary (this test pins the contract, not the micro-opt) but the
    DrainStore.list read still runs; the assertion holds either way.
    """
    db = _open_db(tmp_path)
    try:
        summary = drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))
        assert summary == DrainSummary(_CONNECTOR, 0, 0, 0, 0)
    finally:
        db.close()


def test_drain_keys_on_connector_kind_not_other_source(tmp_path: Path) -> None:
    """Rows under a DIFFERENT source_name are untouched — the drain queries
    on connector_name only.

    Sabotage: query the drain on a hardcoded ''/None source → the other
    connector's rows would leak in and be drained.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, "mine", _ERR_OTHER, source_name=_CONNECTOR)
        _seed_bronze(db, "mine", _MIME_MSWORD, source_name=_CONNECTOR)
        _seed_dead_letter(db, "theirs", _ERR_OTHER, source_name="notion")
        _seed_bronze(db, "theirs", _MIME_MSWORD, source_name="notion")
        db.commit()

        drain_connector_deadletters(db, connector_name=_CONNECTOR, silver=_silver(db))

        # sharepoint row drained; notion row untouched.
        assert _remaining_item_ids(db) == set()
        theirs = {e.item_id for e in DeadLetterStore(db).list("notion")}
        assert theirs == {"theirs"}
    finally:
        db.close()


class _CommitFailsOnceConnection:
    """Duck-typed sqlite3 connection that raises on the FIRST ``commit``.

    Wraps a real connection and forwards ``execute`` / ``rollback`` /
    ``close`` verbatim; the first ``commit`` raises (simulating a wedged
    per-row write), every later ``commit`` forwards. Injected through the
    public ``db`` parameter so the best-effort branch is driven without
    patching any kairix internal — the drain only calls ``execute`` /
    ``commit`` / ``rollback`` on its connection.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self._commits = 0

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._real.execute(*args, **kwargs)

    def commit(self) -> None:
        self._commits += 1
        if self._commits == 1:
            raise sqlite3.OperationalError("simulated per-row commit failure")
        self._real.commit()

    def rollback(self) -> None:
        self._real.rollback()


def test_drain_one_row_error_does_not_abort_rest(tmp_path: Path) -> None:
    """A failure draining one row logs + continues; sibling eligible rows
    still drain (best-effort contract).

    The first eligible candidate's per-row ``commit`` is wedged through a
    duck-typed connection wrapper (the public ``db`` seam — no kairix
    internals patched). That row rolls back and is LEFT; the second
    eligible candidate still drains.

    Sabotage: remove the per-row try/except in ``_drain_one`` → the raised
    ``OperationalError`` propagates and the whole drain aborts; the call
    itself raises and the assertions never run.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, "aaa-boom", _ERR_OTHER)
        _seed_bronze(db, "aaa-boom", _MIME_MSWORD, content_hash="hash-boom")
        _seed_dead_letter(db, "bbb-good", _ERR_OTHER)
        _seed_bronze(db, "bbb-good", _MIME_MSWORD, content_hash="hash-good")
        db.commit()

        # list() orders by last_attempt ASC; both share a timestamp so SQLite
        # falls back to rowid/insert order — 'aaa-boom' is the first drained.
        wrapped = cast("sqlite3.Connection", _CommitFailsOnceConnection(db))
        summary = drain_connector_deadletters(wrapped, connector_name=_CONNECTOR, silver=_silver(wrapped))

        # 'bbb-good' drained; 'aaa-boom' left behind by the swallowed error.
        assert summary.drained == 1
        assert _remaining_item_ids(db) == {"aaa-boom"}
    finally:
        db.close()


# --------------------------------------------------------------------------
# Sync-loop wiring — the drain runs once per connector per tick
# --------------------------------------------------------------------------


def test_sync_loop_drains_seeded_deadletter_end_to_end(tmp_path: Path) -> None:
    """Full ``run_connector_sync_pipeline`` tick drains a pre-seeded,
    permanently-unprocessable dead-letter row for the connector.

    Seeds one corrupt_zip dead-letter (keyed on the obsidian connector
    KIND 'obsidian') with an unsupported-MIME bronze row into the DB the
    sync will open, runs the tick against an empty vault, and asserts the
    row was auto-drained.
    """
    from kairix.worker import ConnectorSyncDeps, run_connector_sync_pipeline

    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "index.sqlite"

    seed_db = sqlite3.connect(str(db_path))
    try:
        create_schema(seed_db)
        # connector.name == 'obsidian' (the KIND) — the drain queries on this.
        _seed_dead_letter(seed_db, "poison-doc", _ERR_CORRUPT_ZIP, source_name="obsidian")
        _seed_bronze(seed_db, "poison-doc", _MIME_MSWORD, content_hash="hash-poison", source_name="obsidian")
        seed_db.commit()
    finally:
        seed_db.close()

    mapping = {
        "topology_v2": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Empty Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                }
            ],
            "cc_pairs": [
                {"id": "obsidian-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian-cc"}
            ],
        }
    }
    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    run_connector_sync_pipeline(deps)

    check_db = sqlite3.connect(str(db_path))
    try:
        remaining = {e.item_id for e in DeadLetterStore(check_db).list("obsidian")}
        status = _documents_media_status(check_db, "hash-poison")
    finally:
        check_db.close()
    assert remaining == set(), "the seeded permanently-unprocessable row should be auto-drained by the tick"
    assert status == _STATUS_SKIPPED


class _DeadLetterListFailsConnection:
    """Duck-typed connection that raises on the drain's enumeration query.

    Forwards every call to the real connection EXCEPT an ``execute`` whose
    SQL reads ``FROM connector_deadletter`` — that one raises, simulating a
    catastrophic enumeration failure inside the drain. The empty-vault
    batch never touches the dead-letter table, so the batch itself succeeds
    and only the drain pass hits the wedge.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "from connector_deadletter" in sql.lower():
            raise sqlite3.OperationalError("simulated dead-letter enumeration failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_sync_loop_absorbs_catastrophic_drain_failure(tmp_path: Path) -> None:
    """A drain-pass failure is swallowed — ``run_connector_sync_pipeline``
    completes and returns a normal result rather than raising.

    Wedges the drain's ``connector_deadletter`` enumeration query through a
    duck-typed connection (the public ``db_factory`` seam). The empty-vault
    batch still completes; the outer ``_auto_drain_connector`` guard absorbs
    the drain exception.

    Sabotage: remove the try/except in ``_auto_drain_connector`` → the
    OperationalError propagates out of the per-connector loop, is caught by
    the loop's own except (logged), but the drain-specific guard line is
    no longer exercised; this test pins the dedicated guard.
    """
    from kairix.worker import ConnectorSyncDeps, ConnectorSyncResult, run_connector_sync_pipeline

    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "index.sqlite"
    seed_db = sqlite3.connect(str(db_path))
    try:
        create_schema(seed_db)
        seed_db.commit()
    finally:
        seed_db.close()

    mapping = {
        "topology_v2": {
            "connectors": [
                {
                    "id": "obsidian-conn",
                    "kind": "obsidian",
                    "name": "Empty Vault",
                    "extractor": "passthrough",
                    "connector_specific_config": {"vault_root": str(vault)},
                }
            ],
            "cc_pairs": [
                {"id": "obsidian-pair", "connector": "obsidian-conn", "credential": None, "name": "obsidian-cc"}
            ],
        }
    }

    def _wedged_db_factory() -> sqlite3.Connection:
        return cast("sqlite3.Connection", _DeadLetterListFailsConnection(sqlite3.connect(str(db_path))))

    deps = ConnectorSyncDeps(
        disabled_fn=lambda: False,
        config_mapping_fn=lambda: mapping,
        db_factory=_wedged_db_factory,
        bronze_root_resolver=lambda: tmp_path / "bronze",
    )

    result = run_connector_sync_pipeline(deps)

    # Sync completed without raising; the empty vault produced zero items.
    assert isinstance(result, ConnectorSyncResult)
    assert result.synced == 0
