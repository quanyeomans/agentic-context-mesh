"""Integration boundary proof: run_expand over the real DocumentRepository (PLA-268).

Single-layer boundary proof (the ``test_<x>_contract.py`` carve-out for
direct component construction): seeds a real on-disk SQLite index with chunk
rows in the exact ``<source_uri>#<seq>`` format the chunk writer produces,
then drives ``run_expand`` through the real ``SQLiteDocumentRepository.get_by_path``
backbone — no fakes on the read path. Pins both the neighbour walk and the
token-budget contract against real SQLite (the tier that broke PLA-270 in CI).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.repository import SQLiteDocumentRepository
from kairix.core.db.schema import create_schema
from kairix.use_cases.expand import ExpandDeps, run_expand

pytestmark = pytest.mark.integration

_URI = "sharepoint://site/contract-doc"
# 10 words per chunk -> 13 estimated tokens each (int(10 * 1.3)).
_NINE_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota"


def _seed(db_path: Path, *, chunks: int) -> None:
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        for seq in range(chunks):
            chunk_hash = f"contract-hash-{seq}"
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


def _deps(db_path: Path) -> ExpandDeps:
    return ExpandDeps(get_chunk=SQLiteDocumentRepository(db_path).get_by_path)


def test_run_expand_reads_real_neighbours(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, chunks=5)

    out = run_expand(_URI, 2, token_budget=10_000, deps=_deps(db_path))

    assert out.error == ""
    assert [c.seq for c in out.chunks] == [0, 1, 2, 3, 4]
    assert [c.seq for c in out.chunks if c.is_match] == [2]
    # Real content travelled through the backbone, not a stub.
    assert "seq0" in out.chunks[0].text


def test_run_expand_honours_token_budget_against_real_index(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, chunks=5)

    out = run_expand(_URI, 2, token_budget=40, deps=_deps(db_path))

    assert out.error == ""
    assert [c.seq for c in out.chunks] == [1, 2, 3]
    assert out.total_tokens == 39


def test_run_expand_missing_chunk_against_real_index(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    _seed(db_path, chunks=2)

    out = run_expand(_URI, 7, deps=_deps(db_path))

    assert out.chunks == []
    assert "no chunk stored" in out.error
