"""F30 outcome test — ``kairix worker preflight`` subprocess surface.

The preflight subcommand is the operator-facing entry point for the
IM-6 fix: a structured persistence-integrity audit that catches the
"68,814 documents, zero FTS rows" failure mode at boot rather than
after a user notices BM25 silently degraded.

This test exercises the subprocess path end-to-end. The BDD scenarios
in ``tests/bdd/test_worker_preflight.py`` drive the in-process kwarg
seam; this test drives the binary the same way an operator (or a
Docker healthcheck) would.

Boundary chain exercised:

  subprocess([python, -m, kairix.cli, worker, preflight, --db-path, ...])
    → kairix/cli.py dispatcher
    → kairix/worker_cli.py:main(["preflight", ...])
    → kairix/worker_cli.py:preflight()
    → kairix/core/db/integrity.py:check_integrity()
    → print(...) on stdout (text or JSON envelope)
    → exit 0 if healthy, 1 if any error gap

Sabotage proofs (executed during authoring):

  * Mutation: drop ``_check_documents_without_fts`` from the
    ``_CHECKS`` tuple in :mod:`kairix.core.db.integrity`.
    Observed failure: ``test_preflight_subprocess_reports_fts_gap``
    failed with exit code 0 + "PASSED" in stdout instead of the
    expected 1 + "documents-without-fts".
    Restoration: re-add ``_check_documents_without_fts`` to the
    tuple.

  * Mutation: change the JSON serialiser to drop the ``healthy``
    key in :func:`kairix.core.db.integrity.report_to_dict`.
    Observed failure:
    ``test_preflight_subprocess_json_mode_emits_envelope`` raised
    ``KeyError: 'healthy'`` on the assertion.
    Restoration: re-add the ``healthy`` key in
    :func:`report_to_dict`.

  * Mutation: rename the ``rebuild_fts`` import inside
    :func:`kairix.worker_cli._auto_heal_gaps` to a no-op stub.
    Observed failure:
    ``test_preflight_subprocess_auto_heal_rebuilds_fts`` failed —
    exit code stayed 1 and the post-heal documents-without-fts
    gap remained.
    Restoration: re-import ``rebuild_fts`` from
    ``kairix.core.db.fts``.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.integration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_fresh_db(path: Path) -> None:
    """Create an empty schema at ``path`` — preflight has no gaps to flag."""
    db = sqlite3.connect(path)
    try:
        create_schema(db, dims=4)
    finally:
        db.close()


def _make_db_with_missing_fts(path: Path) -> None:
    """Seed documents + content + vectors but wipe every FTS row.

    Reproduces the IM-6 dogfood-VM failure mode: real documents with
    no matching FTS rows so BM25 silently degrades to vector-only.
    """
    db = sqlite3.connect(path)
    try:
        create_schema(db, dims=4)
        now = _now()
        for path_value, doc_hash, text in (
            ("a.md", "hash-a", "alpha content"),
            ("b.md", "hash-b", "beta content"),
        ):
            db.execute(
                "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                ("default", path_value, doc_hash, now, now),
            )
            db.execute(
                "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
                (doc_hash, text, now),
            )
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
                (doc_hash,),
            )
        db.execute("DELETE FROM documents_fts")
        db.commit()
    finally:
        db.close()


def _run_preflight(db_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m kairix.cli worker preflight --db-path <db_path> <extra>``."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "worker",
            "preflight",
            "--db-path",
            str(db_path),
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_preflight_subprocess_reports_clean_db(tmp_path: Path) -> None:
    """Clean DB → exit 0 + PASSED on stdout.

    Sabotage proof: see module docstring (drop ``_check_documents_without_fts``
    and this test no longer protects against the IM-6 regression — the
    sibling fts-gap test catches it).
    """
    db_path = tmp_path / "clean.sqlite"
    _make_fresh_db(db_path)

    proc = _run_preflight(db_path)

    assert proc.returncode == 0, (
        f"preflight on a clean DB should exit 0; got {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "PASSED" in proc.stdout, f"expected 'PASSED' marker in stdout: {proc.stdout!r}"


def test_preflight_subprocess_reports_fts_gap(tmp_path: Path) -> None:
    """Active documents missing FTS rows → exit 1 + documents-without-fts.

    The remediation text MUST include ``rebuild-fts`` so the operator
    reading the failed gate knows the next move (F21 contract).
    """
    db_path = tmp_path / "fts-missing.sqlite"
    _make_db_with_missing_fts(db_path)

    proc = _run_preflight(db_path)

    assert proc.returncode == 1, (
        f"preflight with missing FTS rows should exit 1; got {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "documents-without-fts" in proc.stdout, (
        f"expected the documents-without-fts invariant in stdout: {proc.stdout!r}"
    )
    assert "rebuild-fts" in proc.stdout, (
        f"remediation must mention rebuild-fts so operators know the fix: {proc.stdout!r}"
    )


def test_preflight_subprocess_json_mode_emits_envelope(tmp_path: Path) -> None:
    """``--json`` → exit-code reflects health + parseable JSON on stdout.

    The envelope must carry ``healthy`` (boolean) and ``gaps`` (list).
    Asserts on the envelope content, not just on ``returncode == 0``.
    """
    db_path = tmp_path / "fts-missing.sqlite"
    _make_db_with_missing_fts(db_path)

    proc = _run_preflight(db_path, "--json")

    assert proc.returncode == 1, (
        f"json mode preserves the exit-code contract; got {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    parsed = json.loads(proc.stdout)
    assert "healthy" in parsed, f"envelope missing 'healthy' key: {parsed!r}"
    assert "gaps" in parsed, f"envelope missing 'gaps' key: {parsed!r}"
    assert parsed["healthy"] is False, f"expected healthy=False with missing FTS rows; got {parsed!r}"
    invariants = [g["invariant"] for g in parsed["gaps"]]
    assert "documents-without-fts" in invariants, f"expected documents-without-fts gap in envelope; got {invariants!r}"


def test_preflight_subprocess_auto_heal_rebuilds_fts(tmp_path: Path) -> None:
    """``--auto-heal`` runs rebuild_fts, post-audit passes.

    End-to-end: an operator runs ``kairix worker preflight --auto-heal``
    against a degraded DB and walks away with the gap closed.
    """
    db_path = tmp_path / "fts-missing.sqlite"
    _make_db_with_missing_fts(db_path)

    proc = _run_preflight(db_path, "--auto-heal")

    assert proc.returncode == 0, (
        f"auto-heal should clear the documents-without-fts gap; got {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "auto-heal" in proc.stdout, f"expected the auto-heal action to print a status line: {proc.stdout!r}"

    # Confirm post-heal DB state — every active document now has an FTS row.
    db = sqlite3.connect(db_path)
    try:
        docs = db.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()[0]
        fts_rows = db.execute(
            "SELECT COUNT(*) FROM documents d JOIN documents_fts fts ON fts.rowid = d.id WHERE d.active = 1"
        ).fetchone()[0]
    finally:
        db.close()
    assert docs > 0, f"expected seeded documents; got {docs}"
    assert fts_rows == docs, f"auto-heal should leave FTS in sync; documents={docs} fts={fts_rows}"
