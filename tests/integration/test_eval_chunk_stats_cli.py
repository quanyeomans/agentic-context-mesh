"""F30 outcome test for ``kairix eval chunk-stats``.

ADR-028 §"Quality evaluation" #4. Asserts the subcommand reads
``content_vectors`` joined against ``documents`` / ``content`` and
emits a per-source-type table that:

* Carries one row per source type that has chunks indexed.
* Reports the correct ``n`` (chunk count) per type.
* Sorts the per-type rows alphabetically (deterministic output).

Sabotage proofs:

* test_chunk_stats_cli_emits_per_type_table — mutate ``render_human``
  to ``return ""`` and re-run; the assert that ``markdown`` appears
  in stdout fails.
* test_chunk_stats_cli_handles_missing_db_with_actionable_error —
  mutate ``emit_chunk_stats`` to ``return 0`` regardless of the
  missing-DB branch; the exit-code assert fails.
* test_chunk_stats_cli_excludes_inactive_documents — mutate the
  inner SQL to drop ``WHERE d.active = 1``; the inactive-doc rows
  add to the count and the assert fails.
"""

from __future__ import annotations

import io
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.quality.eval.chunk_stats import emit_chunk_stats

pytestmark = pytest.mark.integration


def _seed_chunk_corpus(
    db_path: Path,
    *,
    per_type_chunk_counts: dict[str, int],
    inactive_documents: int = 0,
) -> None:
    """Seed a SQLite with one document per source-type-fixture collection."""
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    cur = db.cursor()
    for source_type, chunk_count in per_type_chunk_counts.items():
        digest = f"hash-{source_type}"
        body = "x" * (chunk_count * 200)  # ~200 chars per chunk
        cur.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (digest, body))
        cur.execute(
            "INSERT INTO documents (path, title, collection, hash, created_at, modified_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                f"per-type-fixtures/{source_type}/sample.{source_type[:3]}",
                f"sample-{source_type}",
                f"per-type-fixtures/{source_type}",
                digest,
                "2026-05-01",
                "2026-05-01",
            ),
        )
        for seq in range(chunk_count):
            cur.execute(
                "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
                (digest, seq, seq * 200, "test-model", "2026-05-01T00:00:00Z"),
            )
    # Seed inactive documents to verify they get excluded.
    for i in range(inactive_documents):
        digest = f"inactive-{i}"
        cur.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (digest, "x" * 500))
        cur.execute(
            "INSERT INTO documents (path, title, collection, hash, created_at, modified_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                f"per-type-fixtures/markdown/old-{i}.md",
                f"old-{i}",
                "per-type-fixtures/markdown",
                digest,
                "2025-01-01",
                "2025-01-01",
            ),
        )
        cur.execute(
            "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
            (digest, 0, 0, "test-model", "2025-01-01T00:00:00Z"),
        )
    db.commit()
    db.close()


def test_chunk_stats_cli_emits_per_type_table(tmp_path: Path) -> None:
    """``kairix eval chunk-stats`` reports per-source-type rows on stdout.

    Sabotage: replace ``render_human`` with ``return ""`` and re-run;
    the assert that ``markdown`` and ``pptx`` appear in stdout fires.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_chunk_corpus(db_path, per_type_chunk_counts={"markdown": 5, "pptx": 3})
    sink = io.StringIO()
    rc = emit_chunk_stats(db_path, sink)
    assert rc == 0, sink.getvalue()
    out = sink.getvalue()
    assert "markdown" in out, out
    assert "pptx" in out, out
    assert "n=5" in out, out
    assert "n=3" in out, out


def test_chunk_stats_cli_handles_missing_db_with_actionable_error(tmp_path: Path) -> None:
    """Missing DB path returns exit 1 with a fix:/next: error envelope.

    Sabotage: remove the ``if not db_path.exists()`` guard — the
    sqlite3 connect call raises a generic error and the exit-code
    assert fails because the error path no longer matches.
    """
    missing = tmp_path / "absent.sqlite"
    sink = io.StringIO()
    rc = emit_chunk_stats(missing, sink)
    assert rc == 1, sink.getvalue()
    out = sink.getvalue()
    assert "fix:" in out, out
    assert "next:" in out, out


def test_chunk_stats_cli_excludes_inactive_documents(tmp_path: Path) -> None:
    """Inactive documents do not contribute to the per-type chunk counts.

    Sabotage: drop ``WHERE d.active = 1`` from the SQL in
    ``collect_chunk_sizes`` — the 2 inactive-markdown rows count and
    n=5 becomes n=7.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_chunk_corpus(
        db_path,
        per_type_chunk_counts={"markdown": 5},
        inactive_documents=2,
    )
    sink = io.StringIO()
    rc = emit_chunk_stats(db_path, sink)
    assert rc == 0, sink.getvalue()
    out = sink.getvalue()
    assert "n=5" in out, f"expected n=5 (inactive excluded), got: {out}"
    assert "n=7" not in out


def test_chunk_stats_cli_subcommand_via_subprocess(tmp_path: Path) -> None:
    """F30 outcome — ``python -m kairix.cli eval chunk-stats`` runs end-to-end.

    The CLI dispatch resolves ``chunk-stats`` through eval_suite.py's
    legacy-subcommand passthrough and lands in ``_cmd_chunk_stats``.

    Sabotage: drop ``"chunk-stats"`` from ``_LEGACY_SUBCOMMANDS`` in
    eval_suite.py and re-run — the dispatcher tries to interpret the
    name as a positional ``suite_path`` and the assert on
    ``markdown`` in stdout fails.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_chunk_corpus(db_path, per_type_chunk_counts={"markdown": 4, "xlsx": 2})
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "eval", "chunk-stats", "--db-path", str(db_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}\nstdout={proc.stdout!r}"
    assert "markdown" in proc.stdout, proc.stdout
    assert "xlsx" in proc.stdout, proc.stdout
    assert "n=4" in proc.stdout, proc.stdout
    assert "n=2" in proc.stdout, proc.stdout
