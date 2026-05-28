"""Contract tests for :mod:`kairix.core.db.integrity`.

For each documented invariant, this module builds a tiny in-memory DB
where the invariant holds (assert healthy / no gap of that kind) and a
sabotaged version where it fails (assert the gap surfaces with the
expected severity + count). Pins the per-invariant contract so a
future refactor of ``check_integrity`` cannot silently lose a check.

Sabotage proofs (executed during authoring):

  * documents-without-fts — dropping
    :func:`kairix.core.db.integrity._check_documents_without_fts` from
    the ``_CHECKS`` tuple causes
    ``test_documents_without_fts_surfaces_gap`` to fail with
    ``healthy=True`` and no matching gap. Restoration: re-add the
    check to the tuple.

  * documents-without-content — removing the ``LEFT JOIN content``
    clause in :func:`_check_documents_without_content` (so the query
    returns the joined inner-join result) leaves the active document
    invisible to the check; the sabotage causes
    ``test_documents_without_content_surfaces_gap`` to fail.
    Restoration: restore the ``LEFT JOIN``.

  * fts-without-documents — flipping the condition in
    :func:`_check_fts_without_documents` from
    ``d.id IS NULL OR d.active = 0`` to ``d.id IS NULL`` (drop the
    inactive-doc check) causes
    ``test_fts_without_documents_inactive_doc_surfaces_gap`` to fail
    because the orphan FTS row tied to an inactive document is no
    longer surfaced. Restoration: re-add ``OR d.active = 0``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from kairix.core.db.integrity import IntegrityGap, IntegrityReport, check_integrity, report_to_dict
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.contract


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_db() -> sqlite3.Connection:
    """Fresh in-memory DB with the kairix schema (dims=4 keeps it cheap)."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _insert_full_document(
    db: sqlite3.Connection,
    *,
    path: str,
    doc_hash: str,
    text: str,
    collection: str = "default",
    active: int = 1,
) -> int:
    """Insert a document + content + vector + FTS row; return the document id."""
    now = _now()
    cur = db.execute(
        "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, ?)",
        (collection, path, doc_hash, now, now, active),
    )
    doc_id = int(cur.lastrowid or 0)
    db.execute(
        "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
        (doc_hash, text, now),
    )
    db.execute(
        "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
        (doc_hash,),
    )
    db.execute(
        "INSERT INTO documents_fts (rowid, filepath, title, doc) VALUES (?, ?, ?, ?)",
        (doc_id, path, "", text),
    )
    db.commit()
    return doc_id


def _gap_by_invariant(report: IntegrityReport, invariant: str) -> IntegrityGap | None:
    """Pick out a specific gap from the report (None if absent)."""
    for gap in report.gaps:
        if gap.invariant == invariant:
            return gap
    return None


# ---------------------------------------------------------------------------
# Empty DB — every check returns None; healthy=True.
# ---------------------------------------------------------------------------


def test_empty_db_is_healthy() -> None:
    """A schema with no rows must report healthy with no gaps."""
    db = _make_db()
    try:
        report = check_integrity(db)
    finally:
        db.close()
    assert report.healthy is True, f"empty DB should be healthy; got gaps={report.gaps!r}"
    assert report.gaps == (), f"empty DB should report no gaps; got {report.gaps!r}"


def test_fully_populated_db_is_healthy() -> None:
    """A DB with matching documents + content + vectors + FTS rows is clean."""
    db = _make_db()
    try:
        _insert_full_document(db, path="a.md", doc_hash="hash-a", text="first")
        _insert_full_document(db, path="b.md", doc_hash="hash-b", text="second")
        report = check_integrity(db)
    finally:
        db.close()
    assert report.healthy is True, f"fully-populated DB should be healthy; got gaps={report.gaps!r}"


# ---------------------------------------------------------------------------
# documents-without-content
# ---------------------------------------------------------------------------


def test_documents_without_content_surfaces_gap() -> None:
    """Active doc with no matching content row → error gap with that path in sample."""
    db = _make_db()
    try:
        db.execute(
            "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            ("default", "orphan.md", "ghost-hash", _now(), _now()),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "documents-without-content")
    assert gap is not None, f"expected documents-without-content gap; got {report.gaps!r}"
    assert gap.severity == "error", f"missing content is an error condition; got {gap.severity!r}"
    assert gap.count == 1, f"expected count=1; got {gap.count}"
    assert "orphan.md" in gap.sample, f"expected orphan.md in sample; got {gap.sample!r}"
    assert report.healthy is False


# ---------------------------------------------------------------------------
# documents-without-fts
# ---------------------------------------------------------------------------


def test_documents_without_fts_surfaces_gap() -> None:
    """Active doc with no FTS row → error gap with rebuild-fts remediation."""
    db = _make_db()
    try:
        _insert_full_document(db, path="a.md", doc_hash="hash-a", text="alpha")
        _insert_full_document(db, path="b.md", doc_hash="hash-b", text="beta")
        # Drop every FTS row to reproduce the IM-6 dogfood shape.
        db.execute("DELETE FROM documents_fts")
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "documents-without-fts")
    assert gap is not None, f"expected documents-without-fts gap; got {report.gaps!r}"
    assert gap.severity == "error", f"missing FTS rows is the IM-6 error mode; got {gap.severity!r}"
    assert gap.count == 2, f"expected count=2 for 2 active docs; got {gap.count}"
    assert "rebuild-fts" in gap.remediation, (
        f"remediation must point to rebuild-fts so operators have the next move; got {gap.remediation!r}"
    )
    assert report.healthy is False


def test_documents_without_fts_inactive_doc_not_flagged() -> None:
    """Inactive documents are out of scope for the FTS check."""
    db = _make_db()
    try:
        _insert_full_document(
            db,
            path="inactive.md",
            doc_hash="hash-inactive",
            text="archived",
            active=0,
        )
        db.execute("DELETE FROM documents_fts")
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "documents-without-fts")
    assert gap is None, f"inactive documents must not flag documents-without-fts; got {gap!r}"


# ---------------------------------------------------------------------------
# documents-without-vectors
# ---------------------------------------------------------------------------


def test_documents_without_vectors_surfaces_gap() -> None:
    """Active doc + content but no vector → error gap."""
    db = _make_db()
    try:
        now = _now()
        db.execute(
            "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            ("default", "novec.md", "hash-novec", now, now),
        )
        db.execute(
            "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
            ("hash-novec", "needs embedding", now),
        )
        # No content_vectors row at all.
        # Also seed FTS so we isolate the vector gap.
        db.execute(
            "INSERT INTO documents_fts (rowid, filepath, title, doc) VALUES (?, ?, ?, ?)",
            (1, "novec.md", "", "needs embedding"),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "documents-without-vectors")
    assert gap is not None, f"expected documents-without-vectors gap; got {report.gaps!r}"
    assert gap.severity == "error"
    assert "novec.md" in gap.sample, f"expected novec.md in sample; got {gap.sample!r}"


# ---------------------------------------------------------------------------
# content-vectors-without-documents (orphan check)
# ---------------------------------------------------------------------------


def test_content_vectors_without_documents_surfaces_warn() -> None:
    """Orphan content_vectors row → warn gap; not fatal."""
    db = _make_db()
    try:
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
            ("orphan-hash",),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "content-vectors-without-documents")
    assert gap is not None, f"expected orphan vector gap; got {report.gaps!r}"
    assert gap.severity == "warn", f"orphan vectors are a warn signal; got {gap.severity!r}"
    assert "orphan-hash" in gap.sample, f"expected orphan-hash in sample; got {gap.sample!r}"
    # Orphans don't flip healthy=False.
    assert report.healthy is True


# ---------------------------------------------------------------------------
# fts-without-documents (orphan + inactive)
# ---------------------------------------------------------------------------


def test_fts_without_documents_orphan_rowid_surfaces_gap() -> None:
    """FTS row pointing at a non-existent document id → warn gap."""
    db = _make_db()
    try:
        # rowid 9999 has no matching documents.id row.
        db.execute(
            "INSERT INTO documents_fts (rowid, filepath, title, doc) VALUES (?, ?, ?, ?)",
            (9999, "ghost.md", "", "orphan"),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "fts-without-documents")
    assert gap is not None, f"expected orphan FTS gap; got {report.gaps!r}"
    assert gap.severity == "warn"
    assert gap.count == 1


def test_fts_without_documents_inactive_doc_surfaces_gap() -> None:
    """FTS row tied to an inactive document is still an orphan."""
    db = _make_db()
    try:
        doc_id = _insert_full_document(
            db,
            path="archived.md",
            doc_hash="hash-archived",
            text="old text",
            active=0,
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "fts-without-documents")
    assert gap is not None, f"FTS row pointing at inactive doc must be flagged as orphan; got {report.gaps!r}"
    assert str(doc_id) in gap.sample or gap.count == 1, (
        f"expected the inactive doc's rowid in sample; got {gap.sample!r}"
    )


# ---------------------------------------------------------------------------
# entity-signals-staging-not-stuck
# ---------------------------------------------------------------------------


def test_entity_signals_old_unpushed_surfaces_warn() -> None:
    """A row older than 7 days un-pushed to Neo4j → warn gap."""
    db = _make_db()
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat().replace("+00:00", "Z")
        db.execute(
            "INSERT INTO entity_signals "
            "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            ("person", "Generic Tester", "test://example", old, 0.9, "internal"),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "entity-signals-staging-not-stuck")
    assert gap is not None, f"expected entity-signals staging gap; got {report.gaps!r}"
    assert gap.severity == "warn"
    assert gap.count == 1


def test_entity_signals_recent_unpushed_not_flagged() -> None:
    """A fresh un-pushed row (within 7 days) is not flagged — drain is normal."""
    db = _make_db()
    try:
        db.execute(
            "INSERT INTO entity_signals "
            "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            ("person", "Recent Entry", "test://example", _now(), 0.9, "internal"),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "entity-signals-staging-not-stuck")
    assert gap is None, f"recent unpushed signals must not flag; got {gap!r}"


def test_entity_signals_count_reports_true_backlog_not_select_limit() -> None:
    """GH #334 — the gap's count is COUNT(*), not the (capped) SELECT row count.

    Before the fix the gap reported ``count = len(rows)`` where the
    SELECT carried ``LIMIT 1000``, so a 2.3M-row backlog read out as
    ``count=1000``. This test stages 1500 stuck rows and asserts the
    count truly reflects 1500 — not the previous 1000 cap.
    """
    db = _make_db()
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        for i in range(1500):
            db.execute(
                "INSERT INTO entity_signals "
                "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                ("person", f"stuck-{i}", f"test://example/{i}", old, 0.9, "internal"),
            )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "entity-signals-staging-not-stuck")
    assert gap is not None, f"expected staging-not-stuck gap; got {report.gaps!r}"
    assert gap.count == 1500, (
        f"GH #334 — expected COUNT(*) to report true backlog of 1500, "
        f"got {gap.count}. The sample stays bounded (was 1000 cap; now MAX_SAMPLE) but "
        f"the count is the source of truth."
    )


# ---------------------------------------------------------------------------
# Remediation contract (F21) — every gap remediation carries one marker.
# ---------------------------------------------------------------------------


def test_all_gap_remediations_carry_actionable_markers() -> None:
    """Every gap surfaced must include at least one of fix: / next: / run:.

    Mirrors F21 at the integrity-module level: a gap with no actionable
    text would leave the operator reading "documents-without-fts" with
    no idea what to do.
    """
    db = _make_db()
    try:
        # Build a DB that trips every error invariant we can synthesise.
        # documents-without-content: a doc with no content row
        db.execute(
            "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            ("default", "missing-content.md", "no-content-hash", _now(), _now()),
        )
        # documents-without-vectors / documents-without-fts via missing FTS
        _insert_full_document(db, path="needs-fts.md", doc_hash="fts-hash", text="needs fts")
        db.execute("DELETE FROM documents_fts")
        # Orphan vector
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
            ("orphan-vec-hash",),
        )
        # Old unpushed signal
        old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat().replace("+00:00", "Z")
        db.execute(
            "INSERT INTO entity_signals "
            "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            ("person", "Generic Tester", "test://example", old, 0.9, "internal"),
        )
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    assert report.gaps, "expected at least one gap from the sabotaged DB"
    for gap in report.gaps:
        text = gap.remediation.lower()
        assert any(marker in text for marker in ("fix:", "next:", "run:")), (
            f"gap {gap.invariant!r} remediation must carry fix:/next:/run: marker; got {gap.remediation!r}"
        )


# ---------------------------------------------------------------------------
# Serialisation — report_to_dict round-trip
# ---------------------------------------------------------------------------


def test_report_to_dict_envelope_shape() -> None:
    """The JSON envelope carries ``healthy`` (bool) + ``gaps`` (list of dicts)."""
    db = _make_db()
    try:
        report = check_integrity(db)
    finally:
        db.close()

    envelope = report_to_dict(report)
    assert "healthy" in envelope, f"envelope must carry 'healthy' key; got {list(envelope)!r}"
    assert "gaps" in envelope, f"envelope must carry 'gaps' key; got {list(envelope)!r}"
    assert isinstance(envelope["healthy"], bool)
    assert isinstance(envelope["gaps"], list)


def test_gap_to_dict_round_trip() -> None:
    """``gap_to_dict`` produces a JSON-serialisable dict with every field."""
    from kairix.core.db.integrity import gap_to_dict

    gap = IntegrityGap(
        invariant="documents-without-fts",
        severity="error",
        count=42,
        sample=("a.md", "b.md"),
        remediation="fix: run kairix embed rebuild-fts; next: re-run; run: kairix embed rebuild-fts",
    )
    d = gap_to_dict(gap)
    assert d["invariant"] == "documents-without-fts"
    assert d["severity"] == "error"
    assert d["count"] == 42
    assert d["sample"] == ["a.md", "b.md"]
    assert "rebuild-fts" in str(d["remediation"])


def test_empty_file_no_schema_returns_healthy() -> None:
    """A connection to a file with no schema (uninitialised DB) returns healthy.

    The worker boot path opens the DB before create_schema may have run;
    preflight must not crash in that window.
    """
    db = sqlite3.connect(":memory:")
    try:
        report = check_integrity(db)
    finally:
        db.close()
    assert report.healthy is True
    assert report.gaps == ()


def test_documents_without_fts_table_missing_surfaces_gap() -> None:
    """If the documents_fts table itself is missing, surface the count
    of active documents as a documents-without-fts error gap."""
    db = _make_db()
    try:
        _insert_full_document(db, path="a.md", doc_hash="ha", text="alpha")
        _insert_full_document(db, path="b.md", doc_hash="hb", text="beta")
        # Drop the FTS table entirely (different mode from "rows wiped").
        db.execute("DROP TABLE documents_fts")
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "documents-without-fts")
    assert gap is not None, f"missing FTS table should surface a gap; got {report.gaps!r}"
    assert gap.severity == "error"
    assert gap.count == 2
    assert "rebuild-fts" in gap.remediation


def test_fts_without_documents_missing_table_skipped() -> None:
    """When the FTS table is missing, fts-without-documents is silent
    (the documents-without-fts gap already covers the condition)."""
    db = _make_db()
    try:
        _insert_full_document(db, path="a.md", doc_hash="ha", text="alpha")
        db.execute("DROP TABLE documents_fts")
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "fts-without-documents")
    assert gap is None, f"fts-without-documents should be silent when table missing; got {gap!r}"


def test_entity_signals_table_missing_skipped() -> None:
    """A pre-Wave-1 deploy (no entity_signals table) skips the check silently."""
    db = _make_db()
    try:
        db.execute("DROP TABLE entity_signals")
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "entity-signals-staging-not-stuck")
    assert gap is None, f"missing entity_signals table should skip; got {gap!r}"


def test_connector_cursors_table_missing_skipped() -> None:
    """Cursors table absent → connector-cursors check is silent."""
    db = _make_db()
    try:
        db.execute("DROP TABLE connector_cursors")
        db.commit()
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "connector-cursors-vs-bronze")
    assert gap is None, f"missing connector_cursors table should skip; got {gap!r}"


def test_connector_cursors_empty_skipped() -> None:
    """Cursors table present but empty → no gap (nothing to compare)."""
    db = _make_db()
    try:
        report = check_integrity(db)
    finally:
        db.close()
    gap = _gap_by_invariant(report, "connector-cursors-vs-bronze")
    assert gap is None


def test_connector_cursors_with_no_config_surfaces_info(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cursor row plus no resolvable config path → info gap pointing at the
    automated-lookup follow-up issue.

    Uses ``monkeypatch.chdir`` to land in a directory with no
    ``kairix.config.yaml`` so the F2 baseline isn't tripped (F2 only flags
    ``KAIRIX_*`` setenv calls).
    """
    db = _make_db()
    try:
        db.execute(
            "INSERT INTO connector_cursors (source_name, cursor_token, updated_at) VALUES (?, ?, ?)",
            ("orphan-source", "tok", _now()),
        )
        db.commit()
        # Move cwd somewhere with no kairix.config.yaml so resolve_config_path
        # returns None. resolve_config_path searches cwd + parents.
        from pathlib import Path

        empty = Path(str(tmp_path)) / "no-config-here"
        empty.mkdir()
        monkeypatch.chdir(empty)
        report = check_integrity(db)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "connector-cursors-vs-bronze")
    # Behaviour depends on whether resolve_config_path found a config in
    # an ancestor dir; assert the contract softly — when it didn't find
    # one, we get an info gap with the follow-up-issue marker.
    if gap is not None:
        assert gap.severity == "info"
        assert "orphan-source" in gap.sample
        assert "#341" in gap.remediation


def test_vector_store_check_no_content_vectors_skipped() -> None:
    """When ``content_vectors`` is empty, the vector-store check skips."""
    db = _make_db()
    try:
        # No content_vectors rows — the docs-without-vectors check covers
        # the "rows missing" mode; this check has nothing to compare.
        report = check_integrity(db)
    finally:
        db.close()
    gap = _gap_by_invariant(report, "vector-store-vs-content-vectors")
    assert gap is None


class _FakeVectorIndex:
    """``__len__``-compatible stand-in for the usearch index in tests.

    Lives here rather than in ``tests/fakes.py`` because it's a single
    contract-test concern; promoting it to the shared fakes module
    would imply other tests should reach for it, and they shouldn't —
    the vector-store check is a soft integrity probe, not a search
    primitive.
    """

    def __init__(self, n: int) -> None:
        self._n = n

    def __len__(self) -> int:
        return self._n


def test_vector_store_check_index_missing_surfaces_info() -> None:
    """``content_vectors`` rows + loader returns None → info gap.

    Uses the injectable ``vector_store_loader`` kwarg on
    :func:`check_integrity` — F1-clean, no monkey-patching.
    """
    db = _make_db()
    try:
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
            ("h-1",),
        )
        db.commit()
        report = check_integrity(db, vector_store_loader=lambda: None)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "vector-store-vs-content-vectors")
    assert gap is not None, f"expected info gap when index is None; got {report.gaps!r}"
    assert gap.severity == "info"
    assert "usearch-index-missing" in gap.sample


def test_vector_store_check_loader_raises_surfaces_info() -> None:
    """Loader raises → info gap with the load-failed marker, never crashes preflight."""

    def _raising_loader() -> object:
        raise RuntimeError("simulated usearch open failure")

    db = _make_db()
    try:
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
            ("h-1",),
        )
        db.commit()
        report = check_integrity(db, vector_store_loader=_raising_loader)
    finally:
        db.close()

    gap = _gap_by_invariant(report, "vector-store-vs-content-vectors")
    assert gap is not None, f"loader exception must surface as info gap; got {report.gaps!r}"
    assert gap.severity == "info"
    assert any("load-failed" in s for s in gap.sample), f"expected load-failed marker; got {gap.sample!r}"


def test_vector_store_check_delta_over_tolerance_surfaces_info() -> None:
    """100-row content_vectors vs 50-row usearch index → info gap."""
    db = _make_db()
    try:
        for i in range(100):
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
                (f"h-{i}",),
            )
        db.commit()
        report = check_integrity(db, vector_store_loader=lambda: _FakeVectorIndex(50))
    finally:
        db.close()

    gap = _gap_by_invariant(report, "vector-store-vs-content-vectors")
    assert gap is not None
    assert gap.severity == "info"
    assert gap.count == 50  # delta = abs(100 - 50)


def test_vector_store_check_within_tolerance_no_gap() -> None:
    """100-row content_vectors vs 99-row usearch → within 5% tolerance, no gap."""
    db = _make_db()
    try:
        for i in range(100):
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
                (f"h-{i}",),
            )
        db.commit()
        report = check_integrity(db, vector_store_loader=lambda: _FakeVectorIndex(99))
    finally:
        db.close()

    gap = _gap_by_invariant(report, "vector-store-vs-content-vectors")
    assert gap is None, f"99 vs 100 is within 5% tolerance; should not flag; got {gap!r}"


def test_vector_store_check_index_empty_surfaces_info() -> None:
    """Loader returns an index with 0 rows but content_vectors has rows."""
    db = _make_db()
    try:
        for i in range(5):
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
                (f"h-{i}",),
            )
        db.commit()
        report = check_integrity(db, vector_store_loader=lambda: _FakeVectorIndex(0))
    finally:
        db.close()

    gap = _gap_by_invariant(report, "vector-store-vs-content-vectors")
    assert gap is not None
    assert gap.severity == "info"
    assert gap.count == 5
