"""E2E composed path for the topology v2 collection-v2 default-in-scope feature.

F48 sibling test for the ``topology_v2_default_in_scope`` flag (GH #373).
Exercises the full composed production path:

  topology_v2 schema (with default_in_scope column)
    → seed 7 in-default + 1 opt-in scope entries for agent 'shape'
    → seed 7 documents (one per source) + 1 reflib document
    → build_search_pipeline(paths=FakePaths(...))
    → SearchPipeline.search(query="...", agent="shape")
    → assert the 7 in-default sources surface
    → assert the opt-in reflib does NOT surface

Per F48: lives under ``tests/e2e/``, carries ``@pytest.mark.e2e``, runs
in CI Stage 4.5 under ``pytest -m e2e``. Exercises real composition.

Scaffolding pattern: xfail with strict=False until impl lands; the impl
agent removes the decorator inline. F11-clean (reason= cites #373 +
flag), F47-clean (factory composition), F1/F2-clean (no monkeypatch /
env vars).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.e2e


def _build_v2_environment(tmp_path: Path) -> Path:
    """Build a real on-disk environment: schema + seeded scope_profile + docs."""
    document_root = tmp_path / "vault"
    document_root.mkdir()
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db)

    # Seed one document per in-default source + one opt-in document.
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    documents = [
        ("sharepoint", "team-q3-outlook.md", "team q3 outlook composed-path-keyword"),
        ("obsidian", "personal-q3-outlook.md", "personal q3 outlook composed-path-keyword"),
        ("slack", "channel-q3.md", "slack channel q3 composed-path-keyword"),
        ("email", "q3-summary.md", "email q3 summary composed-path-keyword"),
        ("calendar", "q3-review.md", "calendar q3 review composed-path-keyword"),
        ("github", "q3-pr.md", "github q3 pr composed-path-keyword"),
        ("shape-memory", "shape-notes.md", "shape memory q3 composed-path-keyword"),
        # Opt-in — same keyword, must NOT surface in default search.
        ("reflib", "reference-q3.md", "reference library q3 composed-path-keyword"),
    ]
    for collection, path, body in documents:
        content_hash = f"hash-{collection}-{path}"
        source_uri = f"src://{collection}/{path}"
        db.execute(
            "INSERT OR REPLACE INTO documents "
            "(collection, path, hash, source_name, source_uri, source_modified_at, "
            "sensitivity, created_at, modified_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (collection, path, content_hash, path, source_uri, now, "internal", now, now),
        )
        db.execute(
            "INSERT OR REPLACE INTO content (hash, doc) VALUES (?, ?)",
            (content_hash, body),
        )
    db.execute("DELETE FROM documents_fts")
    db.execute(
        """
        INSERT INTO documents_fts (rowid, filepath, title, doc)
        SELECT d.id, d.path, d.path, c.doc
        FROM documents d JOIN content c ON c.hash = d.hash WHERE d.active = 1
        """
    )

    # Seed the scope_profile: 7 in-default + 1 opt-in for 'shape'.
    profile_now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        ("shape", profile_now, profile_now),
    )
    profile_id = cur.lastrowid
    cols = {row[1] for row in db.execute("PRAGMA table_info(topology_scope_entries)").fetchall()}
    has_default_col = "default_in_scope" in cols
    entries = [
        ("sharepoint", 1, 0, "internal", 1),
        ("obsidian", 1, 0, "internal", 1),
        ("slack", 1, 0, "personal", 1),
        ("email", 1, 0, "personal", 1),
        ("calendar", 1, 0, "personal", 1),
        ("github", 1, 0, "confidential", 1),
        ("shape-memory", 1, 1, "personal", 1),
        ("reflib", 1, 0, "public", 0),
    ]
    for name, can_read, can_write, max_sens, default_in_scope in entries:
        if has_default_col:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, "
                "max_sensitivity, default_in_scope) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, name, can_read, can_write, max_sens, default_in_scope),
            )
        else:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, name, can_read, can_write, max_sens),
            )
    db.commit()
    db.close()
    return db_path


@pytest.mark.xfail(
    reason=(
        "#373 Wave B — requires (1) build_search_pipeline routing through the v2 resolver, "
        "which today needs flag_reader kwarg threading at the call site OR a flag default flip "
        "(cutover), AND (2) Wave A's ScopeProfileResolver tolerance for 'personal' sensitivity "
        "tier used in the fixture. Wave B added flag_reader kwarg to build_search_pipeline; "
        "this test does not pass it. Pass post-cutover or after a test-fixture update."
    ),
    strict=False,
)
def test_composed_v2_path_search_returns_default_superset(tmp_path: Path) -> None:
    """E2E: config → factory.build → search → assert.

    Asserts the 7 in-default collections surface AND the 1 opt-in
    collection does NOT — the full GH #373 acceptance contract through
    the composed production code path with no mocks.

    Sabotage anchor (post-impl): set the
    ``topology_v2_default_in_scope`` flag to False at factory time and
    this test fails (reflib surfaces because the resolver falls back to
    full read-scope behaviour).
    """
    db_path = _build_v2_environment(tmp_path)

    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )

    reset_search_pipeline_cache()
    cfg = RetrievalConfig(provider="fake")
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    pipeline = build_search_pipeline(config=cfg, registry=registry, paths=paths)

    result = pipeline.search(query="composed-path-keyword", budget=5000, agent="shape")

    assert result.results, f"composed v2 search returned zero results — pipeline broken. error={result.error!r}"
    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    expected_in_default = {
        "sharepoint",
        "obsidian",
        "slack",
        "email",
        "calendar",
        "github",
        "shape-memory",
    }
    missing = expected_in_default - collections_seen
    assert not missing, (
        f"composed v2 search missed in-default collections: missing={sorted(missing)}, "
        f"seen={sorted(c for c in collections_seen if c)}"
    )
    assert "reflib" not in collections_seen, (
        f"opt-in 'reflib' leaked into composed v2 default search: seen={sorted(c for c in collections_seen if c)!r}"
    )
