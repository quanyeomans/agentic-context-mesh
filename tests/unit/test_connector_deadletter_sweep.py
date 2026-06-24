"""Unit tests for PR-5 — the orphaned-source dead-letter drain sweep.

Covers the connector-agnostic per-source core + the all-source sweep in
:mod:`kairix.core.connectors.deadletter_drain` added so dead-letters from
a source whose connector is NO LONGER ACTIVE (the gap the per-connector
auto-drain leaves open) still drain:

* :func:`drain_source_deadletters` — drains an ORPHANED source (no active
  connector instance required): eligible (corrupt_zip / known-unsupported
  MIME) rows clear, ineligible (transient / recoverable) rows are LEFT;
* :func:`drain_all_source_deadletters` — sweeps EVERY distinct source,
  honours ``skip_sources``, is best-effort across sources, idempotent;
* ``dry_run=True`` mutates NOTHING (no clear, no commit, no outcome rows)
  while still reporting the correct per-source / per-class counts;
* :func:`distinct_deadletter_sources` enumerates orphaned sources too.

Eligibility itself (the narrow corrupt_zip OR known-unsupported-MIME rule)
is pinned by ``test_connector_deadletter_drain.py`` — this file pins REACH
across sources, not the predicate. Everything runs against the REAL
``connector_deadletter`` / ``bronze_records`` / ``documents_media`` tables
via ``create_schema`` + the REAL silver processor (F5: public surface only,
no private-name imports).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.connectors.deadletter_drain import (
    DrainSummary,
    distinct_deadletter_sources,
    drain_all_source_deadletters,
    drain_source_deadletters,
)
from kairix.core.connectors.silver import DefaultSilverProcessor, SqliteDocumentsMediaWriter
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit

# An ORPHANED source — its connector is NOT registered / active anywhere.
_ORPHAN = "removed-sharepoint"
_OTHER_ORPHAN = "removed-gdrive"

_ERR_CORRUPT_ZIP = "zipfile.BadZipFile: File is not a zip file"
_ERR_OTHER = "some other weird connector error"
_ERR_TIMEOUT = "Request timed out after 30s"
_ERR_DECODE = "'utf-8' codec can't decode byte 0x80 in position 0"

_MIME_MSWORD = "application/msword"  # KNOWN_UNSUPPORTED → drainable
_MIME_TEXT = "text/plain"  # recoverable → left
_MIME_PNG = "image/png"  # supported binary → left under transient/decode
_STATUS_SKIPPED = "skipped_unsupported"


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "kairix.sqlite"))
    create_schema(db)
    return db


def _seed_dead_letter(db: sqlite3.Connection, source_name: str, item_id: str, last_error: str) -> None:
    db.execute(
        "INSERT INTO connector_deadletter "
        "(source_name, item_id, failure_count, last_error, last_attempt) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_name, item_id, 3, last_error, "2026-06-20T00:00:00Z"),
    )


def _seed_bronze(
    db: sqlite3.Connection,
    source_name: str,
    item_id: str,
    mime: str,
    *,
    content_hash: str | None = "hash-x",
) -> None:
    db.execute(
        "INSERT INTO bronze_records "
        "(source_name, item_id, raw_path, mime, fetched_at, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_name, item_id, f"/bronze/{item_id}", mime, "2026-06-19T00:00:00Z", content_hash),
    )


def _silver(db: sqlite3.Connection) -> DefaultSilverProcessor:
    return DefaultSilverProcessor(documents_media_writer=SqliteDocumentsMediaWriter(db))


def _remaining(db: sqlite3.Connection, source_name: str) -> set[str]:
    return {e.item_id for e in DeadLetterStore(db).list(source_name)}


def _documents_media_count(db: sqlite3.Connection) -> int:
    return int(db.execute("SELECT COUNT(*) FROM documents_media").fetchone()[0])


# --------------------------------------------------------------------------
# drain_source_deadletters — the orphaned-source case (NO active connector)
# --------------------------------------------------------------------------


def test_orphaned_source_drains_eligible_and_leaves_ineligible(tmp_path: Path) -> None:
    """An ORPHANED source's eligible rows drain; ineligible rows are LEFT.

    No active connector instance is constructed anywhere — the core keys on
    the ``source_name`` string alone, so a removed connector's backlog
    drains exactly like an active one's. This is the whole point of PR-5.

    Sabotage: gate the drain on an active-connector lookup → the orphaned
    rows never clear and the ``drained == 2`` / remaining-set assertions
    fail.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "zip-item", _ERR_CORRUPT_ZIP)
        _seed_bronze(db, _ORPHAN, "zip-item", _MIME_MSWORD, content_hash="h-zip")
        _seed_dead_letter(db, _ORPHAN, "doc-item", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "doc-item", _MIME_MSWORD, content_hash="h-doc")
        # recoverable transient on text → must be LEFT.
        _seed_dead_letter(db, _ORPHAN, "txt-item", _ERR_TIMEOUT)
        _seed_bronze(db, _ORPHAN, "txt-item", _MIME_TEXT, content_hash="h-txt")
        db.commit()

        summary = drain_source_deadletters(db, source_name=_ORPHAN, silver=_silver(db))

        assert summary.drained == 2
        assert summary.corrupt_zip == 1
        assert summary.unsupported_mime == 1
        assert summary.left == 1
        assert _remaining(db, _ORPHAN) == {"txt-item"}
    finally:
        db.close()


def test_orphaned_source_dry_run_mutates_nothing(tmp_path: Path) -> None:
    """``dry_run`` reports what WOULD drain but clears / commits NOTHING.

    Sabotage: drop the ``dry_run`` short-circuit in ``_tally_drain`` (let it
    fall through to ``_drain_one``) → the rows clear and the
    ``_remaining == {...}`` / ``documents_media == 0`` assertions fail.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "zip-item", _ERR_CORRUPT_ZIP)
        _seed_bronze(db, _ORPHAN, "zip-item", _MIME_MSWORD, content_hash="h-zip")
        _seed_dead_letter(db, _ORPHAN, "doc-item", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "doc-item", _MIME_MSWORD, content_hash="h-doc")
        db.commit()

        summary = drain_source_deadletters(db, source_name=_ORPHAN, silver=_silver(db), dry_run=True)

        # counts reflect what WOULD drain ...
        assert summary.drained == 2
        assert summary.corrupt_zip == 1
        assert summary.unsupported_mime == 1
        # ... left is the CURRENT (unmutated) depth ...
        assert summary.left == 2
        # ... and the table is untouched: rows survive, zero outcome rows.
        assert _remaining(db, _ORPHAN) == {"zip-item", "doc-item"}
        assert _documents_media_count(db) == 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# distinct_deadletter_sources
# --------------------------------------------------------------------------


def test_distinct_sources_includes_orphans(tmp_path: Path) -> None:
    """Every distinct source_name — orphaned ones included — is enumerated."""
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "a", _ERR_OTHER)
        _seed_dead_letter(db, _OTHER_ORPHAN, "b", _ERR_OTHER)
        _seed_dead_letter(db, _ORPHAN, "c", _ERR_OTHER)  # duplicate source
        db.commit()
        assert distinct_deadletter_sources(db) == (_OTHER_ORPHAN, _ORPHAN)
    finally:
        db.close()


def test_distinct_sources_empty_table_is_empty_tuple(tmp_path: Path) -> None:
    """A clean table enumerates to zero sources (cheap-when-clean)."""
    db = _open_db(tmp_path)
    try:
        assert distinct_deadletter_sources(db) == ()
    finally:
        db.close()


# --------------------------------------------------------------------------
# drain_all_source_deadletters — multi-source reach
# --------------------------------------------------------------------------


def test_all_source_sweep_covers_multiple_sources(tmp_path: Path) -> None:
    """The sweep drains eligible rows across SEVERAL distinct sources.

    Sabotage: make ``drain_all_source_deadletters`` only process the first
    enumerated source → the second source's row survives and the
    ``_remaining(_ORPHAN) == set()`` assertion fails.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _OTHER_ORPHAN, "g-doc", _ERR_OTHER)
        _seed_bronze(db, _OTHER_ORPHAN, "g-doc", _MIME_MSWORD, content_hash="h-g")
        _seed_dead_letter(db, _ORPHAN, "s-zip", _ERR_CORRUPT_ZIP)
        _seed_bronze(db, _ORPHAN, "s-zip", _MIME_MSWORD, content_hash="h-s")
        # one recoverable row left behind in a third logical source.
        _seed_dead_letter(db, _ORPHAN, "s-txt", _ERR_DECODE)
        _seed_bronze(db, _ORPHAN, "s-txt", _MIME_PNG, content_hash="h-png")
        db.commit()

        summaries = drain_all_source_deadletters(db, silver=_silver(db))

        by_source = {s.connector_name: s for s in summaries}
        assert set(by_source) == {_ORPHAN, _OTHER_ORPHAN}
        assert by_source[_OTHER_ORPHAN].drained == 1
        assert by_source[_ORPHAN].drained == 1
        assert by_source[_ORPHAN].left == 1  # the recoverable png row stays
        assert _remaining(db, _OTHER_ORPHAN) == set()
        assert _remaining(db, _ORPHAN) == {"s-txt"}
    finally:
        db.close()


def test_all_source_sweep_skips_named_sources(tmp_path: Path) -> None:
    """``skip_sources`` excludes already-drained sources from the sweep.

    Models the double-drain-avoidance hook: a source the active-connector
    pass already handled this tick is skipped — no summary, no re-scan.

    Sabotage: ignore ``skip_sources`` in the loop → the skipped source's
    eligible row drains and ``_remaining`` becomes empty, failing the
    'still there' assertion; the skipped source also wrongly appears in the
    returned summaries.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "s-doc", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "s-doc", _MIME_MSWORD, content_hash="h-s")
        _seed_dead_letter(db, _OTHER_ORPHAN, "g-doc", _ERR_OTHER)
        _seed_bronze(db, _OTHER_ORPHAN, "g-doc", _MIME_MSWORD, content_hash="h-g")
        db.commit()

        summaries = drain_all_source_deadletters(db, silver=_silver(db), skip_sources=frozenset({_ORPHAN}))

        names = {s.connector_name for s in summaries}
        assert names == {_OTHER_ORPHAN}
        # the skipped source is untouched ...
        assert _remaining(db, _ORPHAN) == {"s-doc"}
        # ... the swept one drained.
        assert _remaining(db, _OTHER_ORPHAN) == set()
    finally:
        db.close()


def test_all_source_sweep_is_idempotent(tmp_path: Path) -> None:
    """A second sweep over the same DB drains nothing more.

    Sabotage: make ``DeadLetterStore.clear`` raise on a missing row → the
    second sweep raises instead of returning zero-drained summaries.
    """
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "s-doc", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "s-doc", _MIME_MSWORD, content_hash="h-s")
        db.commit()

        first = drain_all_source_deadletters(db, silver=_silver(db))
        second = drain_all_source_deadletters(db, silver=_silver(db))

        assert sum(s.drained for s in first) == 1
        # second pass: the source is gone from the table → zero summaries.
        assert second == ()
        assert _remaining(db, _ORPHAN) == set()
    finally:
        db.close()


def test_all_source_sweep_empty_table_is_cheap_noop(tmp_path: Path) -> None:
    """No dead-letter rows → zero summaries, no per-source work."""
    db = _open_db(tmp_path)
    try:
        assert drain_all_source_deadletters(db, silver=_silver(db)) == ()
    finally:
        db.close()


def test_all_source_sweep_dry_run_mutates_nothing(tmp_path: Path) -> None:
    """``dry_run`` across all sources previews counts but clears nothing."""
    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "s-doc", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "s-doc", _MIME_MSWORD, content_hash="h-s")
        _seed_dead_letter(db, _OTHER_ORPHAN, "g-zip", _ERR_CORRUPT_ZIP)
        _seed_bronze(db, _OTHER_ORPHAN, "g-zip", _MIME_TEXT, content_hash="h-g")
        db.commit()

        summaries = drain_all_source_deadletters(db, silver=_silver(db), dry_run=True)

        assert sum(s.drained for s in summaries) == 2
        # nothing mutated.
        assert _remaining(db, _ORPHAN) == {"s-doc"}
        assert _remaining(db, _OTHER_ORPHAN) == {"g-zip"}
        assert _documents_media_count(db) == 0
    finally:
        db.close()


class _EnumFailsForSource:
    """Duck-typed connection whose ``execute`` raises enumerating ONE source.

    Forwards every call to a real connection EXCEPT the dead-letter
    enumeration ``SELECT ... FROM connector_deadletter`` whose params name
    the wedged source — that one raises, simulating a catastrophic
    per-source failure mid-sweep (the failure mode the sweep's per-source
    ``try/except`` exists for; per-ROW clear failures are already absorbed
    one level down by ``_drain_one``). Driven through the public ``db``
    seam (F5 — no kairix internals patched).
    """

    def __init__(self, real: sqlite3.Connection, wedged_source: str) -> None:
        self._real = real
        self._wedged = wedged_source

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        params = args[0] if args else ()
        sql_l = sql.lower()
        names = tuple(params) if isinstance(params, list | tuple) else ()
        if "from connector_deadletter" in sql_l and "group by" not in sql_l and self._wedged in names:
            raise sqlite3.OperationalError("simulated per-source enumeration failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_all_source_sweep_one_source_failure_continues(tmp_path: Path) -> None:
    """One source raising is logged + skipped; sibling sources still drain.

    The wedged source's enumeration ``SELECT`` raises (through the public
    ``db`` seam); ``drain_source_deadletters`` bubbles that up, the sweep's
    per-source ``try/except`` swallows it, and the OTHER source drains
    normally — best-effort across sources.

    Sabotage: remove the per-source ``try/except`` in
    ``drain_all_source_deadletters`` → the OperationalError propagates and
    the whole sweep aborts; ``_remaining(_OTHER_ORPHAN)`` is never drained
    and the call itself raises.
    """
    from typing import cast

    db = _open_db(tmp_path)
    try:
        # _OTHER_ORPHAN sorts first (g < r), so it drains BEFORE the wedge.
        _seed_dead_letter(db, _OTHER_ORPHAN, "g-doc", _ERR_OTHER)
        _seed_bronze(db, _OTHER_ORPHAN, "g-doc", _MIME_MSWORD, content_hash="h-g")
        _seed_dead_letter(db, _ORPHAN, "s-doc", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "s-doc", _MIME_MSWORD, content_hash="h-s")
        db.commit()

        wrapped = cast("sqlite3.Connection", _EnumFailsForSource(db, _ORPHAN))
        summaries = drain_all_source_deadletters(wrapped, silver=_silver(wrapped))

        names = {s.connector_name for s in summaries}
        assert names == {_OTHER_ORPHAN}, "only the healthy source produced a summary"
        assert _remaining(db, _OTHER_ORPHAN) == set(), "the healthy source still drained"
        assert _remaining(db, _ORPHAN) == {"s-doc"}, "the wedged source's row was left behind"
    finally:
        db.close()


def test_drain_connector_alias_matches_source_core(tmp_path: Path) -> None:
    """The active-connector alias is a thin pass-through to the source core.

    Pins that ``drain_connector_deadletters`` (the per-active-connector
    seam at ``worker._run_one_connector_batch``) and
    ``drain_source_deadletters`` produce the same outcome — the refactor is
    behaviour-preserving for the active path.
    """
    from kairix.core.connectors.deadletter_drain import drain_connector_deadletters

    db = _open_db(tmp_path)
    try:
        _seed_dead_letter(db, _ORPHAN, "doc-item", _ERR_OTHER)
        _seed_bronze(db, _ORPHAN, "doc-item", _MIME_MSWORD, content_hash="h-doc")
        db.commit()

        summary = drain_connector_deadletters(db, connector_name=_ORPHAN, silver=_silver(db))

        assert summary == DrainSummary(_ORPHAN, drained=1, corrupt_zip=0, unsupported_mime=1, left=0)
        assert _remaining(db, _ORPHAN) == set()
    finally:
        db.close()
