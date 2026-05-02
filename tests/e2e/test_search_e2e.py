"""
E2E test: hybrid search pipeline with real BM25 backend and mock vector search.

Skipped unless KAIRIX_E2E=1 is set.

Run manually:
  KAIRIX_E2E=1 python3 -m pytest tests/e2e/ -v -s
"""

import os
import sqlite3

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("KAIRIX_E2E") != "1",
        reason="E2E tests skipped unless KAIRIX_E2E=1",
    ),
]


@pytest.fixture()
def fts5_db(tmp_path):
    """Create an in-memory-style SQLite DB with FTS5 and test documents."""
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))

    db.execute("""
        CREATE TABLE documents (
            hash TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT,
            collection TEXT
        )
        """)

    db.execute("""
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            title, content, path UNINDEXED, hash UNINDEXED
        )
        """)

    docs = [
        (
            "hash_001",
            "/vault/shared/facts.md",
            "Shared Facts",
            "knowledge-shared",
            "The VM has 4 vCPUs and 16 GB RAM. Infrastructure runs on Azure.",
        ),
        (
            "hash_002",
            "/vault/builder/patterns.md",
            "Builder Patterns",
            "knowledge-shared",
            "Use trash instead of rm for safety. Always check before deleting.",
        ),
        (
            "hash_003",
            "/vault/projects/alpha.md",
            "Project Alpha",
            "vault-projects",
            "Project Alpha is the main client engagement for Q2 2026.",
        ),
        (
            "hash_004",
            "/vault/resources/runbook-deploy.md",
            "Deployment Runbook",
            "knowledge-shared",
            "Step 1: Pull latest code. Step 2: Run tests. Step 3: Deploy to staging.",
        ),
        (
            "hash_005",
            "/vault/knowledge/observability.md",
            "Observability Guide",
            "knowledge-shared",
            "Arize Phoenix is used for LLM observability and tracing.",
        ),
    ]

    for hash_val, path, title, collection, content in docs:
        db.execute(
            "INSERT INTO documents (hash, path, title, collection) VALUES (?, ?, ?, ?)",
            (hash_val, path, title, collection),
        )
        db.execute(
            "INSERT INTO documents_fts (title, content, path, hash) VALUES (?, ?, ?, ?)",
            (title, content, path, hash_val),
        )

    db.commit()
    db.close()
    return db_path


@pytest.fixture()
def _patch_search_deps(fts5_db, monkeypatch):
    """Patch BM25 to use our FTS5 DB and disable vector search."""
    import sqlite3 as _sqlite3

    from kairix.core.search import bm25 as bm25_mod

    _original_bm25_search = bm25_mod.bm25_search

    def _fts5_bm25_search(query, collections=None, limit=10, date_filter_paths=None):
        """BM25-like search using our local FTS5 DB."""
        from kairix.core.search.bm25 import BM25Result, _normalise_fts_query

        normalised = _normalise_fts_query(query)
        if not normalised.strip():
            return []

        db = _sqlite3.connect(str(fts5_db))
        try:
            # Simple FTS5 MATCH query
            tokens = normalised.split()
            fts_query = " OR ".join(tokens)
            rows = db.execute(
                "SELECT d.path, d.title, snippet(documents_fts, 1, '', '', '...', 30) AS snippet, "
                "rank * -1.0 AS score, d.collection "
                "FROM documents_fts "
                "JOIN documents d ON d.hash = documents_fts.hash "
                "WHERE documents_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except Exception:
            return []
        finally:
            db.close()

        results = []
        for path, title, snippet, score, collection in rows:
            results.append(
                BM25Result(
                    file=path,
                    title=title or "",
                    snippet=snippet or "",
                    score=float(score) if score else 0.0,
                    collection=collection or "",
                )
            )
        return results

    monkeypatch.setattr("kairix.core.search.hybrid.bm25_search", _fts5_bm25_search)
    monkeypatch.setattr("kairix.core.search.hybrid._run_vector_search", lambda *a, **kw: [])
    monkeypatch.setattr(
        "kairix.core.search.hybrid._get_neo4j",
        lambda: type("C", (), {"available": False})(),
    )
    monkeypatch.setattr("kairix.core.search.hybrid._log_search_event", lambda *a, **kw: None)


class TestSearchE2E:
    """End-to-end tests for the hybrid search pipeline with real BM25."""

    @pytest.mark.integration
    def test_search_returns_results(self, _patch_search_deps):
        """Hybrid search returns results from FTS5 BM25 backend."""
        from kairix.core.search.hybrid import search

        result = search("infrastructure Azure VM")
        assert len(result.results) > 0, "Expected at least one result"

    @pytest.mark.integration
    def test_search_intent_classified(self, _patch_search_deps):
        """Search result includes a classified intent."""
        from kairix.core.search.hybrid import SearchResult, search
        from kairix.core.search.intent import QueryIntent

        result = search("how to deploy to staging")
        assert isinstance(result, SearchResult)
        assert isinstance(result.intent, QueryIntent)

    @pytest.mark.integration
    def test_search_bm25_results_present(self, _patch_search_deps):
        """BM25 results are present in the fused output."""
        from kairix.core.search.hybrid import search

        result = search("observability tracing Arize")
        assert result.bm25_count > 0, "Expected BM25 to contribute results"

    @pytest.mark.integration
    def test_search_no_errors(self, _patch_search_deps):
        """Search completes without error."""
        from kairix.core.search.hybrid import search

        result = search("project engagement Q2")
        assert result.error == ""

    @pytest.mark.integration
    def test_search_returns_search_result_type(self, _patch_search_deps):
        """search() always returns SearchResult dataclass."""
        from kairix.core.search.hybrid import SearchResult, search

        result = search("deployment runbook steps")
        assert isinstance(result, SearchResult)
        assert hasattr(result, "query")
        assert hasattr(result, "intent")
        assert hasattr(result, "results")
