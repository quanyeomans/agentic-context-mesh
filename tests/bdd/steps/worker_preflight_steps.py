"""Step definitions for ``tests/bdd/features/worker_preflight.feature``.

Drives :func:`kairix.worker_cli.main` directly through its
``db_path`` injection seam — no env-var monkeypatching, no real
filesystem state outside the per-scenario tmp_path.

The scenarios cover three shapes:

  1. Healthy DB → ``preflight`` exits 0 and prints "PASSED".
  2. DB with FTS rows missing for active documents → ``preflight``
     exits 1 with the ``documents-without-fts`` invariant + a
     remediation that mentions ``rebuild-fts``.
  3. Same DB shape + ``--auto-heal`` → ``preflight`` runs
     :func:`kairix.core.db.fts.rebuild_fts`, exits 0, and the FTS
     index ends with one row per active document.

Sabotage-proof anchors (executed during authoring, see commit body):

  * Renaming :func:`_check_documents_without_fts` to a no-op breaks
    scenario 2 (no gap surfaced; exit 0).
  * Replacing :func:`rebuild_fts` with a no-op breaks scenario 3
    (auto-heal exits 1; FTS still empty).
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.worker_cli import main as worker_main

pytestmark = pytest.mark.bdd


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def _preflight_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario harness — owns the tmp DB path + captured stdio."""
    return {
        "db_path": tmp_path / "preflight-index.sqlite",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
    }


# ---------------------------------------------------------------------------
# Given — seed a database into the documented shape
# ---------------------------------------------------------------------------


@given("a fresh kairix database with no documents")
def _given_fresh_db(_preflight_state: dict[str, Any]) -> None:
    """Create an empty schema on disk so preflight has nothing to flag."""
    db = sqlite3.connect(_preflight_state["db_path"])
    try:
        create_schema(db, dims=4)
    finally:
        db.close()


@given("a kairix database with active documents missing FTS rows")
def _given_db_with_missing_fts(_preflight_state: dict[str, Any]) -> None:
    """Insert documents + content + vectors, then DELETE every FTS row.

    Reproduces the IM-6 dogfood-VM shape: real documents, real content,
    real vectors, but the FTS5 index lost its rows. Preflight must
    surface the gap.
    """
    db = sqlite3.connect(_preflight_state["db_path"])
    try:
        create_schema(db, dims=4)
        now = _now()
        for path, doc_hash, text in (
            ("a.md", "hash-a", "alpha content"),
            ("b.md", "hash-b", "beta content"),
            ("c.md", "hash-c", "gamma content"),
        ):
            db.execute(
                "INSERT INTO documents (collection, path, hash, created_at, modified_at, active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                ("default", path, doc_hash, now, now),
            )
            db.execute(
                "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
                (doc_hash, text, now),
            )
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, 0, 0)",
                (doc_hash,),
            )
        # Wipe every FTS row to reproduce the cutover failure mode.
        db.execute("DELETE FROM documents_fts")
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# When — invoke worker_cli preflight
# ---------------------------------------------------------------------------


def _invoke_preflight(_preflight_state: dict[str, Any], *, auto_heal: bool) -> None:
    """Run preflight against the per-scenario tmp DB; capture stdio + rc."""
    out = io.StringIO()
    err = io.StringIO()
    # Use the in-process kwarg seam so the BDD step doesn't shell out;
    # the subprocess outcome test in tests/integration covers the
    # subprocess path separately (F30).
    argv = ["preflight"]
    if auto_heal:
        argv.append("--auto-heal")
    # Inject the tmp db via the kwarg precedence path (worker_cli.main
    # honours db_path= over the --db-path arg over the default).
    import sys as _sys

    captured_out = _sys.stdout
    captured_err = _sys.stderr
    _sys.stdout = out
    _sys.stderr = err
    try:
        rc = worker_main(argv, db_path=_preflight_state["db_path"])
    finally:
        _sys.stdout = captured_out
        _sys.stderr = captured_err
    _preflight_state["exit_code"] = rc
    _preflight_state["stdout"] = out.getvalue()
    _preflight_state["stderr"] = err.getvalue()


@when("the operator runs worker preflight")
def _when_run_preflight(_preflight_state: dict[str, Any]) -> None:
    _invoke_preflight(_preflight_state, auto_heal=False)


@when("the operator runs worker preflight with auto-heal")
def _when_run_preflight_auto_heal(_preflight_state: dict[str, Any]) -> None:
    _invoke_preflight(_preflight_state, auto_heal=True)


# ---------------------------------------------------------------------------
# Then — assertions on exit code, stdout content, post-heal DB state
# ---------------------------------------------------------------------------


@then(parsers.parse("the preflight exit code is {expected:d}"))
def _then_exit_code(_preflight_state: dict[str, Any], expected: int) -> None:
    actual = _preflight_state["exit_code"]
    assert actual == expected, (
        f"expected exit code {expected}; got {actual}\n"
        f"stdout={_preflight_state['stdout']!r}\nstderr={_preflight_state['stderr']!r}"
    )


@then(parsers.parse('the preflight output contains "{phrase}"'))
def _then_output_contains(_preflight_state: dict[str, Any], phrase: str) -> None:
    stdout = _preflight_state["stdout"]
    assert phrase in stdout, f"expected {phrase!r} in stdout: {stdout!r}"


@then("the preflight remediation mentions rebuild-fts")
def _then_remediation_mentions_rebuild(_preflight_state: dict[str, Any]) -> None:
    stdout = _preflight_state["stdout"]
    # Sabotage proof: changing the remediation string in
    # _check_documents_without_fts to drop "rebuild-fts" fails this.
    assert "rebuild-fts" in stdout, f"remediation must mention rebuild-fts; got: {stdout!r}"


@then("the FTS index now has rows for every active document")
def _then_fts_repopulated(_preflight_state: dict[str, Any]) -> None:
    db = sqlite3.connect(_preflight_state["db_path"])
    try:
        docs_row = db.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()
        fts_row = db.execute(
            "SELECT COUNT(*) FROM documents d JOIN documents_fts fts ON fts.rowid = d.id WHERE d.active = 1"
        ).fetchone()
    finally:
        db.close()
    docs = int(docs_row[0]) if docs_row else 0
    fts = int(fts_row[0]) if fts_row else 0
    # Sabotage proof: stubbing rebuild_fts to a no-op leaves fts = 0 while
    # docs > 0; this assertion fires.
    assert docs > 0, f"expected active documents in the seeded DB; got {docs}"
    assert fts == docs, f"auto-heal should leave FTS in sync; documents={docs} fts={fts}"
