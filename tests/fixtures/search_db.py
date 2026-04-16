"""sqlite-vec search index fixtures for tests.

This is the SEARCH INDEX only (BM25 + vectors). Not the entity store.
Entity tests use FakeNeo4jClient from neo4j_mock.py.
"""
import pytest
import sqlite3
import tempfile
from pathlib import Path

# 20 canonical test documents covering all intent categories
_TEST_DOCUMENTS = [
    # temporal
    {"path": "daily/2026-04-10.md", "title": "Daily Note 2026-04-10", "body": "Completed FEAT-081 implementation last week. Board updated."},
    {"path": "daily/2026-04-09.md", "title": "Daily Note 2026-04-09", "body": "Sprint review happened yesterday. Reviewed all open tickets."},
    # entity
    {"path": "entities/openclaw.md", "title": "OpenClaw", "body": "OpenClaw is an AI agent platform. It provides memory_search and tool orchestration."},
    {"path": "entities/avanade.md", "title": "Avanade", "body": "Avanade is a Microsoft services partner. Connected to OpenClaw via PARTNER_OF."},
    # procedural
    {"path": "runbooks/how-to-run-embed.md", "title": "How to run the embedding pipeline", "body": "Step 1: Run kairix embed. Step 2: Check recall. Step 3: Verify index."},
    {"path": "runbooks/how-to-restart.md", "title": "How to restart services", "body": "Run systemctl restart kairix. Verify with kairix onboard check."},
    # keyword
    {"path": "features/FEAT-081.md", "title": "FEAT-081", "body": "FEAT-081 implementation status: done. Merged in sprint 2A."},
    {"path": "features/FEAT-082.md", "title": "FEAT-082", "body": "FEAT-082 entity expansion. Target: sprint 2B."},
    # semantic
    {"path": "strategy/infra-cost.md", "title": "Infrastructure Cost Strategy", "body": "Infrastructure cost optimisation strategy: use spot instances, auto-scaling, and reserved capacity."},
    {"path": "strategy/tech-debt.md", "title": "Technical Debt Strategy", "body": "Approach to managing technical debt: quarterly reviews, sprint allocation, automated detection."},
    # multi_hop
    {"path": "research/openclaw-avanade-partnership.md", "title": "OpenClaw Avanade Partnership", "body": "The connection between OpenClaw and Avanade is a strategic partnership for enterprise AI deployment."},
    {"path": "research/entity-graph.md", "title": "Entity Graph Overview", "body": "Multi-hop relationships between organisations and people enable contextual search."},
    # extra semantic docs
    {"path": "notes/architecture.md", "title": "Architecture Overview", "body": "Kairix uses hybrid BM25 + vector search with RRF fusion for ranking."},
    {"path": "notes/performance.md", "title": "Performance Notes", "body": "NDCG@10 target is 0.78 overall. Temporal category requires temporal_boost."},
    {"path": "notes/sprint-2a.md", "title": "Sprint 2A Notes", "body": "Sprint 2A scope: S1-B, DEBT-1, TEST-1, EGQ-3, EGQ-4, FEAT-129, R3."},
    {"path": "notes/domains.md", "title": "Domain Model", "body": "Four bounded contexts: search, entity, embedding, briefing. Strict producer/consumer contracts."},
    {"path": "notes/testing.md", "title": "Test Strategy", "body": "Test pyramid: smoke, BDD, contract, integration, unit. pytest-bdd for acceptance tests."},
    {"path": "notes/neo4j.md", "title": "Neo4j Graph", "body": "Neo4j is the canonical entity store. Cypher queries via graph.client.cypher()."},
    {"path": "notes/embedding.md", "title": "Embedding Notes", "body": "Azure text-embedding-3-large produces 1536-dim float vectors stored in sqlite-vec."},
    {"path": "notes/curator.md", "title": "Curator Health", "body": "Curator health checks entity graph completeness: missing vault_path, stale entities, synthesis failures."},
]


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a tmp path for a SQLite DB."""
    return tmp_path / "test-kairix.db"


@pytest.fixture
def search_db(tmp_db_path):
    """Minimal in-memory SQLite DB with BM25 search table. No sqlite-vec extension required."""
    conn = sqlite3.connect(str(tmp_db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            agent TEXT DEFAULT 'shared',
            created_date TEXT,
            tokens INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(path, title, body, content='documents')")
    conn.commit()
    return conn


@pytest.fixture
def seeded_search_db(search_db):
    """Search DB seeded with 20 canonical test documents covering all intent categories."""
    for doc in _TEST_DOCUMENTS:
        search_db.execute(
            "INSERT OR REPLACE INTO documents (path, title, body) VALUES (?, ?, ?)",
            (doc["path"], doc["title"], doc["body"]),
        )
        search_db.execute(
            "INSERT INTO documents_fts (path, title, body) VALUES (?, ?, ?)",
            (doc["path"], doc["title"], doc["body"]),
        )
    search_db.commit()
    return search_db
