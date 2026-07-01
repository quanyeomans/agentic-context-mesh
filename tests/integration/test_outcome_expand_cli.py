"""F30 outcome test for ``kairix expand`` (PLA-268).

Boots the whole ``python -m kairix.cli expand`` binary against a real
on-disk SQLite index seeded with a multi-chunk document, then asserts the
neighbour window comes back. The retrieval path is the real
``SQLiteDocumentRepository.get_by_path`` backbone wired through the
``--db-path`` subprocess seam — no env vars, no fakes.

A second test runs with no ``--db-path`` so the production default seam
(``_default_get_chunk`` → the resolved worker index) executes and degrades
gracefully on an empty store rather than crashing.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.integration

_URI = "m365://outcome-doc"
_NINE_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota"


def _seed_index(db_path: Path, *, chunks: int) -> None:
    """Write ``chunks`` chunk rows for one source_uri into a real index."""
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        for seq in range(chunks):
            chunk_hash = f"hash-{seq}"
            db.execute(
                "INSERT INTO documents (collection, path, hash, source_uri, sensitivity, active) "
                "VALUES (?, ?, ?, ?, 'public', 1)",
                ("team-notes", f"{_URI}#{seq}", chunk_hash, _URI),
            )
            db.execute(
                "INSERT INTO content (hash, doc) VALUES (?, ?)",
                (chunk_hash, f"{_NINE_WORDS} seq{seq}"),
            )
        db.commit()
    finally:
        db.close()


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", "expand", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_expand_cli_returns_neighbour_window(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _seed_index(db_path, chunks=5)

    result = _run_cli(_URI, "2", "--token-budget", "10000", "--db-path", str(db_path), "--json")

    assert "Traceback" not in result.stderr, f"crashed:\n{result.stderr}"
    envelope = json.loads(result.stdout)
    assert envelope["error"] == ""
    seqs = [c["seq"] for c in envelope["chunks"]]
    assert seqs == [0, 1, 2, 3, 4], f"expected full window; got {seqs!r}"
    matched = [c["seq"] for c in envelope["chunks"] if c["is_match"]]
    assert matched == [2]


def test_expand_cli_missing_chunk_exits_nonzero(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _seed_index(db_path, chunks=2)

    result = _run_cli(_URI, "9", "--db-path", str(db_path), "--json")

    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    envelope = json.loads(result.stdout)
    assert "no chunk stored" in envelope["error"]


def test_expand_cli_default_index_degrades_gracefully() -> None:
    """No ``--db-path`` → the production default seam runs against the
    resolved (empty/fresh) worker index and returns an actionable miss
    rather than a traceback. Exercises ``_default_get_chunk``."""
    result = _run_cli("kairix://does-not-exist", "0", "--json")

    assert "Traceback" not in result.stderr, f"default seam crashed:\n{result.stderr}"
    envelope = json.loads(result.stdout)
    assert "chunks" in envelope
    # Empty store → a miss, surfaced as an error string (never a crash).
    assert envelope["error"] != ""


def test_expand_cli_source_uri_only_default_index_degrades_gracefully() -> None:
    """No seq AND no ``--db-path`` → the source_uri-only path runs the
    production by-prefix default seam (``_default_list_chunk_seqs``) against
    the resolved (empty/fresh) worker index and returns an actionable miss
    with the no-finer-chunks signal, never a traceback."""
    result = _run_cli("kairix://does-not-exist", "--json")

    assert "Traceback" not in result.stderr, f"default by-prefix seam crashed:\n{result.stderr}"
    envelope = json.loads(result.stdout)
    assert envelope["chunks"] == []
    # Empty store, source_uri-only → no finer chunks + an actionable error.
    assert envelope["no_finer_chunks"] is True
    assert envelope["error"] != ""
