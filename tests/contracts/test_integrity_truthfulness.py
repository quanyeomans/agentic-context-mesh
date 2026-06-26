"""F71 truthfulness contracts for every preflight in :mod:`kairix.core.db.integrity`.

Why this file exists
--------------------
GH #334 shipped the ``_check_entity_signals_staging_not_stuck`` preflight
with a ``SELECT ... LIMIT 1000`` and reported ``count = len(rows)``. A
2.3M-row backlog read out as ``count = 1000``; operators saw "small
enough to ignore" and the drain stall persisted for years. The fix
(landed earlier this session) switched the count to ``COUNT(*)``.

F71 (`scripts/checks/check_f71_preflight_truthfulness.py`) enforces the
mechanical rule that prevents that pattern from recurring anywhere
else: every preflight check that reports a ``count`` must have a
paired test here named
``test_<check_function_name>_count_equals_ground_truth``. Each test
seeds N >= 1500 rows matching the predicate the preflight reads, then
asserts ``gap.count == SELECT COUNT(*) FROM <table> WHERE <same
predicate>``. The seed size is generously larger than any historical
LIMIT cap so an under-reporting bug surfaces as a concrete count
mismatch ("preflight reported 1000; SELECT COUNT(*) reports 1500").

How we test through the public surface (F5)
-------------------------------------------
F5 forbids tests from importing ``_check_*`` names directly. The public
surface is ``check_integrity(db)`` which returns an ``IntegrityReport``
whose ``gaps`` tuple carries one ``IntegrityGap`` per preflight that
fired. Each gap carries a stable ``invariant`` identifier (a public
kebab-case string the CLI / runbook docs quote verbatim), so the test
picks out a specific preflight's gap by that string — never by the
private function name. This mirrors the existing pattern in
``tests/contracts/test_integrity_invariants.py``.

The F71 check script knows the binding between preflight function name
and gap invariant identifier; the contract test's function name
encodes the preflight function name (``test_<check_function_name>_...``)
so the static scan can match without the test itself reaching into the
private function.

Exemptions
----------
The single F71 exemption today is
``_check_vector_store_vs_content_vectors``. Its gap's ``count`` field
carries the delta between the SQL ``content_vectors`` row count and the
external usearch index length — there is no single SQL aggregate that
ground-truths that delta. The exemption is recorded on the function in
``kairix/core/db/integrity.py`` with a
``# F71-truthfulness-exempt: ...`` comment. F71 detects the marker and
skips the check.

Sabotage proofs (executed during authoring)
-------------------------------------------
Each truthfulness test was sabotage-proven by mutating the underlying
preflight to under-report:

  * ``_check_entity_signals_staging_not_stuck`` — swap the
    ``count = total_count`` line for ``count = len(sample_rows)``
    (the historical bug shape — sample size 5 instead of true backlog).
    The seeded 1500-row fixture causes the paired test to fail with
    ``preflight reported 5; SELECT COUNT(*) reports 1500``.
    Restoration: revert the edit.

  * ``_check_documents_without_content`` — slice the result to
    ``rows[:100]`` before the gap construction. The 1500-row seed
    causes the paired test to fail with
    ``preflight reported 100; SELECT COUNT(*) reports 1500``.
    Restoration: drop the slice.

  * ``_check_content_vectors_without_documents`` — slice
    ``rows[:50]``. Same shape; paired test fails with
    ``preflight reported 50; SELECT COUNT(*) reports 1500``.
    Restoration: drop the slice.

Plus F71 itself was sabotage-proven by renaming
``test__check_documents_without_fts_count_equals_ground_truth`` and
running ``python3 scripts/checks/check_f71_preflight_truthfulness.py`` —
the gate fires with
``FAIL [arch:f71-preflight-truthfulness] — new violation(s):
kairix/core/db/integrity.py::_check_documents_without_fts`` and the
REMEDIATION block prints with the fix/next/run markers, Pass example,
and Forbidden example. Restoration: re-add the test under its real name.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kairix.core.db.integrity import (
    IntegrityGap,
    IntegrityReport,
    check_integrity,
)
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.contract

# Generously larger than any historical LIMIT cap (the GH #334 bug
# capped at 1000). Seeding 1500 rows means an under-reporting mutation
# surfaces as a concrete count mismatch ("preflight reported 1000;
# SELECT COUNT(*) reports 1500") rather than a near-miss.
_SEED_N = 1500

# Mirrors ``kairix.core.db.integrity._STAGING_STUCK_DAYS``. Pinned here
# (rather than imported) so the contract stays public-surface only.
_STAGING_STUCK_DAYS = 7

# Map from preflight function name (encoded into the test name) to the
# public ``invariant`` identifier each preflight emits in its
# ``IntegrityGap``. The CLI surfaces these identifiers verbatim in
# ``kairix worker preflight`` output and the runbook documents them, so
# they ARE the public boundary — picking a gap out of the report by its
# invariant id is F5-clean.
_PREFLIGHT_INVARIANT = {
    "_check_documents_without_content": "documents-without-content",
    "_check_documents_without_fts": "documents-without-fts",
    "_check_documents_without_vectors": "documents-without-vectors",
    "_check_documents_without_embedded_vector": "documents-without-embedded-vector",
    "_check_content_vectors_without_documents": "content-vectors-without-documents",
    "_check_fts_without_documents": "fts-without-documents",
    "_check_entity_signals_staging_not_stuck": "entity-signals-staging-not-stuck",
    "_check_connector_cursors_vs_bronze": "connector-cursors-vs-bronze",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stuck_cutoff_iso(extra_days: int = 7) -> str:
    """Return an ISO timestamp guaranteed older than the staging-stuck cutoff."""
    delta_days = _STAGING_STUCK_DAYS + extra_days
    return (datetime.now(timezone.utc) - timedelta(days=delta_days)).isoformat().replace("+00:00", "Z")


def _make_db() -> sqlite3.Connection:
    """Fresh in-memory DB with the kairix schema (dims=4 keeps it cheap)."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _gap_by_invariant(report: IntegrityReport, invariant: str) -> IntegrityGap | None:
    """Pick a specific gap from the report; None if absent.

    Mirrors the helper in ``tests/contracts/test_integrity_invariants.py``.
    """
    for gap in report.gaps:
        if gap.invariant == invariant:
            return gap
    return None


# ---------------------------------------------------------------------------
# Helpers — bulk seeders, one per predicate the preflights read.
# ---------------------------------------------------------------------------


def _seed_documents_without_content(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n active documents with no matching ``content`` row."""
    now = _now_iso()
    rows = [("default", f"missing-content-{i}.md", f"ghost-hash-{i}", now, now, 1) for i in range(n)]
    db.executemany(
        "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()


def _seed_documents_without_fts(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n active documents (+ content + vector) but no FTS rows."""
    now = _now_iso()
    for i in range(n):
        db.execute(
            "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            ("default", f"no-fts-{i}.md", f"no-fts-hash-{i}", now, now),
        )
        db.execute(
            "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
            (f"no-fts-hash-{i}", f"text-{i}", now),
        )
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
            (f"no-fts-hash-{i}",),
        )
    # Drop every FTS row to put us in the IM-6 failure shape.
    db.execute("DELETE FROM documents_fts")
    db.commit()


def _seed_documents_without_vectors(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n active documents (+ content + FTS) with no ``content_vectors`` rows."""
    now = _now_iso()
    for i in range(n):
        cur = db.execute(
            "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            ("default", f"no-vec-{i}.md", f"no-vec-hash-{i}", now, now),
        )
        doc_id = int(cur.lastrowid or 0)
        db.execute(
            "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
            (f"no-vec-hash-{i}", f"text-{i}", now),
        )
        db.execute(
            "INSERT INTO documents_fts (rowid, filepath, title, doc) VALUES (?, ?, ?, ?)",
            (doc_id, f"no-vec-{i}.md", "", f"text-{i}"),
        )
    db.commit()


def _seed_documents_without_embedded_vector(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n active documents (+ content + a model-NULL PLACEHOLDER vector).

    Each document has a ``content_vectors`` row written as the chunk writer
    writes it pre-embed — ``(hash, seq, pos)`` with ``model`` NULL. So the
    presence-only ``documents-without-vectors`` check is SATISFIED (a vector
    row exists), while the state check ``documents-without-embedded-vector``
    fires (no row with ``model`` set). This is exactly the chunk-0 #627 shape:
    a placeholder that the discovery query never promoted.
    """
    now = _now_iso()
    for i in range(n):
        db.execute(
            "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) VALUES (?, ?, ?, ?, ?, 1)",
            ("default", f"placeholder-only-{i}.md", f"placeholder-hash-{i}", now, now),
        )
        db.execute(
            "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
            (f"placeholder-hash-{i}", f"text-{i}", now),
        )
        # model NULL → an un-promoted placeholder, not a real embedding.
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
            (f"placeholder-hash-{i}",),
        )
    db.commit()


def _seed_orphan_content_vectors(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n ``content_vectors`` rows whose hash has no matching document."""
    rows = [(f"orphan-vec-hash-{i}", 0, 0) for i in range(n)]
    db.executemany(
        "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
        rows,
    )
    db.commit()


def _seed_orphan_fts(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n FTS rows whose rowid does not appear in ``documents``.

    Uses rowids well above any auto-incremented document id so the
    preflight's LEFT JOIN reports them as orphan.
    """
    rows = [(100_000 + i, f"ghost-fts-{i}.md", "", f"orphan-{i}") for i in range(n)]
    db.executemany(
        "INSERT INTO documents_fts (rowid, filepath, title, doc) VALUES (?, ?, ?, ?)",
        rows,
    )
    db.commit()


def _seed_stuck_entity_signals(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n ``entity_signals`` rows old enough to count as 'stuck'."""
    old = _stuck_cutoff_iso(extra_days=7)
    rows = [("person", f"stuck-{i}", f"test://example/{i}", old, 0.9, "internal", 0) for i in range(n)]
    db.executemany(
        "INSERT INTO entity_signals "
        "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()


def _seed_connector_cursors(db: sqlite3.Connection, *, n: int) -> None:
    """Insert n ``connector_cursors`` rows for the orphan-config branch."""
    now = _now_iso()
    rows = [(f"orphan-source-{i}", f"tok-{i}", now) for i in range(n)]
    db.executemany(
        "INSERT INTO connector_cursors (source_name, cursor_token, updated_at) VALUES (?, ?, ?)",
        rows,
    )
    db.commit()


def _vector_loader_returns_none() -> object | None:
    """Inject-seam for ``check_integrity`` so the vector-store check stays silent.

    The truthfulness tests focus on one preflight at a time; we don't
    want the (legitimately F71-exempt) vector-store probe to add noise
    to the report. Returning ``None`` reports an info gap with sample
    ``usearch-index-missing`` — it carries an ``invariant`` distinct
    from any we assert on, so it doesn't interfere.
    """
    return None


# ---------------------------------------------------------------------------
# Truthfulness contracts — one per preflight check.
# ---------------------------------------------------------------------------


def test__check_documents_without_content_count_equals_ground_truth() -> None:
    """F71: gap.count for documents-without-content equals COUNT(*) ground truth.

    Predicate: ``documents d LEFT JOIN content c ON c.hash = d.hash
    WHERE d.active = 1 AND c.hash IS NULL``. Sabotage-prove by slicing
    ``rows[:100]`` in the preflight; the 1500-row seed makes the
    masking obvious.
    """
    db = _make_db()
    try:
        _seed_documents_without_content(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_documents_without_content"])
        ground_truth_row = db.execute(
            "SELECT COUNT(*) FROM documents d "
            "LEFT JOIN content c ON c.hash = d.hash "
            "WHERE d.active = 1 AND c.hash IS NULL"
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface a gap when 1500 orphan-content documents are seeded"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); "
        f"got {ground_truth} — fixture is wrong if you see this"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_documents_without_content reported count={gap.count}; "
        f"SELECT COUNT(*) FROM documents LEFT JOIN content ... reports {ground_truth}. "
        f"The preflight is hiding the true scale — see GH #334."
    )
    assert gap.severity == "error", (
        f"F71 sibling-assert: documents-without-content is a hard error; got {gap.severity!r}"
    )


def test__check_documents_without_fts_count_equals_ground_truth() -> None:
    """F71: gap.count for documents-without-fts equals COUNT(*) ground truth.

    Predicate: ``documents d LEFT JOIN documents_fts fts ON fts.rowid =
    d.id WHERE d.active = 1 AND fts.rowid IS NULL``. The IM-6 dogfood
    regression shape — every active document missing an FTS row.
    """
    db = _make_db()
    try:
        _seed_documents_without_fts(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_documents_without_fts"])
        ground_truth_row = db.execute(
            "SELECT COUNT(*) FROM documents d "
            "LEFT JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE d.active = 1 AND fts.rowid IS NULL"
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface a gap when 1500 active docs have no FTS rows"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_documents_without_fts reported count={gap.count}; "
        f"SELECT COUNT(*) reports {ground_truth}. The IM-6 dogfood regression scale "
        f"(68,814 active docs / 0 FTS rows) would re-mask if this drifts."
    )
    assert gap.severity == "error"


def test__check_documents_without_vectors_count_equals_ground_truth() -> None:
    """F71: gap.count for documents-without-vectors equals COUNT(*) ground truth.

    Predicate: ``documents d LEFT JOIN content_vectors v ON v.hash =
    d.hash WHERE d.active = 1 AND v.hash IS NULL``.
    """
    db = _make_db()
    try:
        _seed_documents_without_vectors(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_documents_without_vectors"])
        ground_truth_row = db.execute(
            "SELECT COUNT(*) FROM documents d "
            "LEFT JOIN content_vectors v ON v.hash = d.hash "
            "WHERE d.active = 1 AND v.hash IS NULL"
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface gap when 1500 docs lack vectors"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_documents_without_vectors reported count={gap.count}; "
        f"SELECT COUNT(*) reports {ground_truth}. Masking the embedding-backlog scale "
        f"would let the dogfood VM ship vector-less docs invisibly."
    )
    assert gap.severity == "error"


def test__check_documents_without_embedded_vector_count_equals_ground_truth() -> None:
    """F71: gap.count for documents-without-embedded-vector equals COUNT(*) ground truth.

    Predicate (the STATE check): ``documents d LEFT JOIN content_vectors v ON
    v.hash = d.hash AND v.model IS NOT NULL WHERE d.active = 1 AND v.hash IS
    NULL``. Every seeded doc carries a model-NULL placeholder, so the join's
    ``AND v.model IS NOT NULL`` finds no embedded vector and all n are counted.
    Sabotage-prove by slicing ``rows[:100]`` in the preflight; the 1500-row
    seed surfaces the masking as a concrete mismatch.
    """
    db = _make_db()
    try:
        _seed_documents_without_embedded_vector(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_documents_without_embedded_vector"])
        ground_truth_row = db.execute(
            "SELECT COUNT(*) FROM documents d "
            "LEFT JOIN content_vectors v ON v.hash = d.hash AND v.model IS NOT NULL "
            "WHERE d.active = 1 AND v.hash IS NULL"
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface a gap when 1500 placeholder-only documents are seeded"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_documents_without_embedded_vector reported count={gap.count}; "
        f"SELECT COUNT(*) reports {ground_truth}. Masking the un-embedded-placeholder "
        f"backlog (the chunk-0 #627 class) would let documents ship vector-unsearchable "
        f"while the presence-only check stays green."
    )
    assert gap.severity == "warn", (
        f"F71 sibling-assert: documents-without-embedded-vector is a warn signal "
        f"(transient embed lag must never block strict preflight); got {gap.severity!r}"
    )


def test__check_content_vectors_without_documents_count_equals_ground_truth() -> None:
    """F71: gap.count for orphan content_vectors equals COUNT(DISTINCT hash) ground truth.

    Predicate: ``SELECT COUNT(DISTINCT v.hash) FROM content_vectors v
    LEFT JOIN documents d ON d.hash = v.hash WHERE d.hash IS NULL``.
    """
    db = _make_db()
    try:
        _seed_orphan_content_vectors(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_content_vectors_without_documents"])
        ground_truth_row = db.execute(
            "SELECT COUNT(DISTINCT v.hash) FROM content_vectors v "
            "LEFT JOIN documents d ON d.hash = v.hash "
            "WHERE d.hash IS NULL"
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface gap when 1500 orphan content_vectors rows are seeded"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: COUNT(DISTINCT hash) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_content_vectors_without_documents reported count={gap.count}; "
        f"SELECT COUNT(DISTINCT v.hash) reports {ground_truth}. The orphan-vector "
        f"backlog must read out at full scale so operators can size the pruning job."
    )
    assert gap.severity == "warn", f"F71 sibling-assert: orphan content_vectors are a warn signal; got {gap.severity!r}"


def test__check_fts_without_documents_count_equals_ground_truth() -> None:
    """F71: gap.count for orphan FTS rows equals COUNT(*) ground truth.

    Predicate: ``documents_fts fts LEFT JOIN documents d ON d.id =
    fts.rowid WHERE d.id IS NULL OR d.active = 0``.
    """
    db = _make_db()
    try:
        _seed_orphan_fts(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_fts_without_documents"])
        ground_truth_row = db.execute(
            "SELECT COUNT(*) FROM documents_fts fts "
            "LEFT JOIN documents d ON d.id = fts.rowid "
            "WHERE d.id IS NULL OR d.active = 0"
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface gap when 1500 orphan FTS rows are seeded"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_fts_without_documents reported count={gap.count}; "
        f"SELECT COUNT(*) reports {ground_truth}. Under-reporting orphan FTS rows "
        f"masks index drift after bulk document deletions."
    )
    assert gap.severity == "warn"


def test__check_entity_signals_staging_not_stuck_count_equals_ground_truth() -> None:
    """F71: gap.count for stuck entity_signals equals COUNT(*) ground truth.

    Predicate: ``entity_signals WHERE pushed_to_neo4j = 0 AND
    modified_at < <cutoff>``. THE GH #334 anti-pattern lived here — the
    previous code shipped ``SELECT ... LIMIT 1000`` and used
    ``len(rows)`` as the count, capping a 2.3M backlog at 1000. The
    rewritten preflight uses ``COUNT(*)``; this test guarantees the
    bound-vs-truth gap stays closed.
    """
    db = _make_db()
    try:
        _seed_stuck_entity_signals(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_entity_signals_staging_not_stuck"])
        # The preflight reads ``modified_at < cutoff`` where ``cutoff`` is
        # ``now - _STAGING_STUCK_DAYS``. The seed stamps every row with
        # ``now - (_STAGING_STUCK_DAYS + 7)``, so reading the truth back
        # with ``modified_at < now`` is exact (and resilient to the second
        # or two that elapse between seed and assert).
        ground_truth_row = db.execute(
            "SELECT COUNT(*) FROM entity_signals WHERE pushed_to_neo4j = 0 AND modified_at < ?",
            (_now_iso(),),
        ).fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    assert gap is not None, "F71: preflight must surface gap when 1500 stuck signals are seeded"
    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71 / GH #334: _check_entity_signals_staging_not_stuck reported count={gap.count}; "
        f"SELECT COUNT(*) reports {ground_truth}. This is the exact failure mode that "
        f"masked the 2.3M-row Neo4j drain backlog for years — never let count drift "
        f"below COUNT(*) again."
    )
    assert gap.severity == "warn"


def test__check_connector_cursors_vs_bronze_count_equals_ground_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F71: gap.count for orphan connector cursors equals COUNT(*) ground truth.

    The preflight only emits a gap when the config loader returns no
    config path, and the gap's ``count`` is ``len(rows)`` from
    ``SELECT source_name FROM connector_cursors`` (no LIMIT, so it
    equals ``SELECT COUNT(*) FROM connector_cursors``). We force the
    no-config branch by chdir-ing into a directory with no
    ``kairix.config.yaml``. ``monkeypatch.chdir`` does not touch any
    ``KAIRIX_*`` env var (F2-clean) and the preflight is driven through
    ``check_integrity`` (no kairix-internal substitution, F1/F5-clean).
    """
    empty = tmp_path / "no-config-here"
    empty.mkdir()
    monkeypatch.chdir(empty)

    db = _make_db()
    try:
        _seed_connector_cursors(db, n=_SEED_N)
        report = check_integrity(db, vector_store_loader=_vector_loader_returns_none)
        gap = _gap_by_invariant(report, _PREFLIGHT_INVARIANT["_check_connector_cursors_vs_bronze"])
        ground_truth_row = db.execute("SELECT COUNT(*) FROM connector_cursors").fetchone()
        ground_truth = int(ground_truth_row[0]) if ground_truth_row else 0
    finally:
        db.close()

    if gap is None:
        # An ancestor directory of tmp_path carries a kairix.config.yaml
        # (the resolve_config_path search walks upward); the no-config
        # branch is unreachable here, so there's nothing for F71 to check
        # truthfulness against. Skip with the rationale captured —
        # F11-clean because the rationale is explicit and the skip is
        # behaviour-driven, not silence-driven.
        pytest.skip(
            "F71: ancestor dir provided a kairix.config.yaml; orphan-cursor "
            "branch is unreachable from this CWD — truthfulness contract is "
            "vacuous when no info gap is emitted."
        )

    assert ground_truth == _SEED_N, (
        f"F71 self-check: SELECT COUNT(*) should match the seed ({_SEED_N}); got {ground_truth}"
    )
    assert gap.count == ground_truth, (
        f"F71: _check_connector_cursors_vs_bronze reported count={gap.count}; "
        f"SELECT COUNT(*) FROM connector_cursors reports {ground_truth}. The "
        f"orphan-cursor info gap must read out at full scale so operators see "
        f"the real cleanup surface."
    )
    assert gap.severity == "info"


# ---------------------------------------------------------------------------
# Exemption documentation — a single read-only assertion so the file
# explicitly lists which preflights are NOT covered and why.
# ---------------------------------------------------------------------------


def test_truthfulness_exemption_inventory_is_complete() -> None:
    """Document every F71 exemption in one place.

    F71 detects ``# F71-truthfulness-exempt: <rationale>`` comments on
    the preflight definition and skips the check. This test asserts the
    canonical exemption list matches what's in
    ``kairix/core/db/integrity.py`` so a future contributor doesn't
    silently grow the exemption set without updating the inventory.
    """
    from kairix.core.db import integrity

    integrity_path = Path(integrity.__file__)
    integrity_source = integrity_path.read_text(encoding="utf-8")
    exempted_today = ("_check_vector_store_vs_content_vectors",)
    for name in exempted_today:
        assert f"def {name}(" in integrity_source, (
            f"F71 exemption inventory: expected to find ``def {name}(`` in "
            f"integrity.py — has the function been renamed? Update both the "
            f"exemption comment and this inventory."
        )
        marker_idx = integrity_source.find("F71-truthfulness-exempt")
        assert marker_idx >= 0, (
            "F71 exemption inventory: expected at least one ``# F71-truthfulness-exempt:`` comment in integrity.py"
        )
        # Anchor the marker to the function — the marker AND the def line
        # must appear within a 600-char window so the rationale lives
        # next to the function it exempts.
        window = integrity_source[marker_idx : marker_idx + 600]
        assert name in window, (
            f"F71 exemption inventory: ``F71-truthfulness-exempt`` marker should "
            f"sit within 600 chars of ``def {name}(`` so the rationale lives "
            f"next to the function it exempts"
        )
