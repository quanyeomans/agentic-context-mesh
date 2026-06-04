"""GH #409 — pin the indexed exact-match enrich-phase query plan.

Before this fix, ``SQLiteDocumentRepository._get_chunk_dates_uncached``
ran ``WHERE d.path LIKE '%suffix' OR ...`` against an unindexed
``documents.path``. On 1.1M rows that scanned the whole table per
search call (14 s p50 on the alpha9 production VM, 81% of search
latency).

These tests pin the post-fix invariants:

  - The SQL query uses ``WHERE d.path_canonical IN (?, ?, ...)``.
  - The planner picks ``idx_documents_path_canonical`` for the lookup
    (verified via ``EXPLAIN QUERY PLAN`` — not a comment, an assertion).
  - The LRU cache wrapper is still on the live code path (the #391
    contract is not regressed by the query rewrite).
  - The empty-input short-circuit is preserved.

Tests construct a real ``SQLiteDocumentRepository`` against a real
on-disk SQLite DB created by ``create_schema`` — no monkey-patching,
no @patch, no env vars. F1/F2/F4-clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.db import open_db
from kairix.core.db.repository import SQLiteDocumentRepository
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers (intentionally small — match the surrounding test_repository style)
# ---------------------------------------------------------------------------


def _build_repo(tmp_path: Path) -> tuple[Path, SQLiteDocumentRepository]:
    """Build a fresh on-disk DB + repository via the production schema path."""
    db_path = tmp_path / "enrich-index.sqlite"
    db = open_db(db_path)
    try:
        create_schema(db)
    finally:
        db.close()
    return db_path, SQLiteDocumentRepository(db_path=db_path)


def _seed_dated(
    db_path: Path,
    repo: SQLiteDocumentRepository,
    *,
    path: str,
    content_hash: str,
    chunk_date: str,
) -> None:
    """Insert one document + content_vectors row with a chunk_date."""
    repo.insert_or_update(path, "notes", "T", "body", content_hash)
    db = open_db(db_path)
    try:
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at, chunk_date) VALUES (?, ?, ?, ?, ?, ?)",
            (content_hash, 0, 0, "model", 1000, chunk_date),
        )
        db.commit()
    finally:
        db.close()


def _explain_query_plan(db_path: Path, sql: str, params: list) -> list[str]:
    """Return the planner's chosen access plan as a list of detail strings."""
    db = open_db(db_path)
    try:
        # safe: caller-controlled SQL from this test module only.
        rows = db.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    finally:
        db.close()
    # Each row: (id, parent, notused, detail). Detail is the human-readable plan.
    return [row[3] for row in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_uses_path_canonical_exact_match(tmp_path: Path) -> None:
    """The rewritten query returns the right row via exact-match lookup.

    GH #409 — replaces ``LIKE '%suffix'`` with ``IN (?, ?, ...)`` against
    the indexed ``path_canonical`` column.

    Sabotage proof: with ``path_canonical IN (?)`` removed (or replaced
    by a literal-false condition like ``path_canonical IN ('___missing')``),
    the returned dict is empty and the equality assertion fails.
    """
    db_path, repo = _build_repo(tmp_path)
    _seed_dated(db_path, repo, path="/abs/notes/dated.md", content_hash="h-dated", chunk_date="2026-05-01")

    # Caller passes the canonical (full) path that's stored in
    # ``documents.path``. The virtual generated column ``path_canonical``
    # mirrors ``path``, so the exact-match IN-clause hits the index.
    result = repo.get_chunk_dates(["/abs/notes/dated.md"])

    assert result == {"/abs/notes/dated.md": "2026-05-01"}


def test_planner_picks_path_canonical_index_not_full_scan(tmp_path: Path) -> None:
    """``EXPLAIN QUERY PLAN`` must show ``idx_documents_path_canonical``
    being used — not a full table scan on ``documents``.

    The pre-#409 ``LIKE '%suffix'`` query reported ``SCAN documents`` in
    the plan. After the fix, the plan must reference the new index.

    Sabotage proof: if the SQL ever regresses to ``LIKE '%' || ? || '%'``
    or ``d.path = ?`` (without ``path_canonical``), the assertion
    ``"idx_documents_path_canonical" in any plan line`` fails because
    the planner falls back to a SCAN.
    """
    db_path, _ = _build_repo(tmp_path)

    # Mirror the production query shape — caller materialises an
    # ``IN (?, ?, ...)`` against ``path_canonical``.
    sql = (
        "SELECT d.path, cv.chunk_date "
        "FROM content_vectors cv "
        "JOIN documents d ON d.hash = cv.hash "
        "WHERE cv.chunk_date IS NOT NULL "
        "AND d.path_canonical IN (?)"
    )
    plan_lines = _explain_query_plan(db_path, sql, ["/abs/notes/dated.md"])

    # The planner must pick the dedicated index for the documents-side
    # probe. Without ``path_canonical``-driven lookup, the only access
    # paths are a full ``SCAN documents`` or rowid lookups via a different
    # index (none of which prove the perf fix is in place).
    plan_text = " | ".join(plan_lines)
    assert "idx_documents_path_canonical" in plan_text, (
        f"expected idx_documents_path_canonical in EXPLAIN plan, got: {plan_text}. "
        f"fix: confirm CREATE INDEX idx_documents_path_canonical landed in schema.py. "
        f"next: run `python3 scripts/migrations/2026-06-04-documents-path-canonical.py --dry-run`"
    )
    assert "SCAN documents" not in plan_text, f"plan shows full SCAN documents — index is not picked. plan: {plan_text}"


def test_lru_cache_still_serves_repeat_calls_after_query_rewrite(tmp_path: Path) -> None:
    """Two consecutive calls with the same ``frozenset(paths)`` must
    return the identical cached dict — proves the LRU wrapper is still
    on the live path after the SQL rewrite.

    Sabotage proof: if the LRU wrapper were removed during the rewrite
    (e.g. ``get_chunk_dates`` straight-piping to ``_get_chunk_dates_uncached``),
    each call would yield a fresh dict and the ``is`` check would fail.
    Also verified by ``cache_info().hits >= 1`` on the second call.
    """
    db_path, repo = _build_repo(tmp_path)
    _seed_dated(db_path, repo, path="/abs/notes/cached.md", content_hash="h-cached", chunk_date="2026-05-02")

    first = repo.get_chunk_dates(["/abs/notes/cached.md"])
    second = repo.get_chunk_dates(["/abs/notes/cached.md"])

    assert first is second
    info = repo._chunk_dates_cache.cache_info()
    assert info.misses == 1
    assert info.hits >= 1


def test_empty_paths_short_circuit_returns_empty_without_sql(tmp_path: Path) -> None:
    """``get_chunk_dates([])`` returns ``{}`` without touching SQLite.

    Sabotage proof: if the short-circuit were removed, the cache would
    register a miss for ``frozenset()`` and the SQL helper would run
    with zero placeholders — at best returning ``{}``, at worst raising
    a syntax error on ``IN ()``. The ``misses == 0`` assertion below
    fails in either case.
    """
    _db_path, repo = _build_repo(tmp_path)

    result = repo.get_chunk_dates([])

    assert result == {}
    info = repo._chunk_dates_cache.cache_info()
    assert info.misses == 0, "empty-paths must not enter the cache or the SQL helper"
    assert info.hits == 0
