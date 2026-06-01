"""Integration tests for v2 collection default-in-scope search (GH #373).

Drives the full :func:`kairix.core.factory.build_search_pipeline` factory
against a seeded SQLite + topology_v2 schema with the
``topology_v2_default_in_scope`` feature flag flipped through both
branches.

Pins (per docs/architecture/collection-v2-implementation-plan.md):

  * 6 in-default sources seeded → no-collections search returns all 6.
  * reflib seeded as opt-in → no-collections search excludes it.
  * Explicit ``collections=['reflib']`` retrieves the reflib doc.
  * Own-memory in default search; other-agent memory excluded.
  * Cross-agent explicit request → empty + F21-shaped error.
  * Flag OFF → legacy DefaultCollectionResolver wired.
  * Flag ON → TopologyV2CollectionResolver wired.

Scaffolding pattern: every test xfails with strict=False until the impl
agent removes the decorator. F47-clean: every pipeline is constructed via
``build_search_pipeline(paths=FakePaths(...))``; no direct construction.
F1/F2-clean: no monkeypatch, no env vars.
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

pytestmark = pytest.mark.integration


def _bootstrap_db(db_path: Path) -> sqlite3.Connection:
    """Create schema, return an open connection ready for seeding."""
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.commit()
    return db


def _seed_documents(db: sqlite3.Connection, *, docs: list[tuple[str, str, str]]) -> None:
    """Seed N documents.

    Each entry is ``(collection, path, body_text)``. Writes to
    ``documents`` + ``content`` + ``documents_fts`` so BM25 search has
    something to match. ``documents.collection`` is the column the search
    pipeline filters on when a collection list comes back from the
    resolver.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for collection, path, body in docs:
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
        FROM documents d JOIN content c ON c.hash = d.hash
        WHERE d.active = 1
        """
    )
    db.commit()


def _seed_scope_profile_seven_in_default_one_opt_in(
    db: sqlite3.Connection,
    *,
    actor_id: str = "shape",
) -> None:
    """Seed the 7-in-default + 1-opt-in scope profile for ``actor_id``."""
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        (actor_id, now, now),
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
        (f"{actor_id}-memory", 1, 1, "personal", 1),
        ("reflib", 1, 0, "public", 0),  # opt-in
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


def _build_pipeline(tmp_path: Path) -> tuple[Path, FakeProviderRegistry]:
    """Helper — bootstrap DB + paths; the test calls
    :func:`build_search_pipeline` with the result.

    Returns ``(db_path, registry)`` so the test can seed extra rows after
    schema creation, then construct the pipeline against ``FakePaths``.
    """
    db_path = tmp_path / "index.sqlite"
    return db_path, FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})


@pytest.fixture(autouse=True)
def _reset_pipeline_cache() -> None:
    """Each integration test starts with a fresh pipeline cache so the
    flag-branch assertions don't ride a previous test's cached resolver.
    """
    reset_search_pipeline_cache()


def _make_paths(tmp_path: Path, db_path: Path) -> object:
    return FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_search_with_no_collections_returns_results_from_every_in_default_source(
    tmp_path: Path,
) -> None:
    """End-to-end: 6 docs seeded across 6 sources, all in-default,
    agent search with no collections specified → every doc retrievable.

    Sabotage anchor (post-impl): drop the ``default_only=True`` arg from
    the Adapter's collections=None path and this test fails (reflib
    would also surface, breaking the in-default invariant).
    """
    db_path, registry = _build_pipeline(tmp_path)
    db = _bootstrap_db(db_path)
    _seed_documents(
        db,
        docs=[
            ("sharepoint", "team-notes.md", "team notes quarterly outlook"),
            ("obsidian", "vault-note.md", "personal vault quarterly outlook"),
            ("slack", "channel-msg.md", "slack channel quarterly outlook"),
            ("email", "subject.md", "email subject quarterly outlook"),
            ("calendar", "event.md", "calendar event quarterly outlook"),
            ("github", "pr.md", "github pull-request quarterly outlook"),
        ],
    )
    _seed_scope_profile_seven_in_default_one_opt_in(db, actor_id="shape")
    db.close()

    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=_make_paths(tmp_path, db_path),
    )

    result = pipeline.search(query="quarterly outlook", budget=3000, agent="shape")

    assert result.results, (
        f"in-default search returned zero results — composed pipeline broken. "
        f"error={result.error!r} bm25_count={result.bm25_count}"
    )
    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    expected = {"sharepoint", "obsidian", "slack", "email", "calendar", "github"}
    missing = expected - collections_seen
    assert not missing, (
        f"in-default search missed one or more sources: missing={sorted(missing)}, "
        f"seen={sorted(c for c in collections_seen if c)}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_search_with_no_collections_excludes_opt_in_collection(tmp_path: Path) -> None:
    """reflib has 100% keyword match but is opt-in — default search
    must NOT return it.

    The opt-in collection is in the actor's read scope, so the legacy
    behaviour (return everything readable) would surface it. The new
    behaviour MUST drop it from the no-collections path.
    """
    db_path, registry = _build_pipeline(tmp_path)
    db = _bootstrap_db(db_path)
    _seed_documents(
        db,
        docs=[
            ("sharepoint", "sp.md", "team notes term-only-in-default"),
            ("reflib", "ref.md", "reference library term-only-in-default"),
        ],
    )
    _seed_scope_profile_seven_in_default_one_opt_in(db, actor_id="shape")
    db.close()

    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=_make_paths(tmp_path, db_path),
    )

    result = pipeline.search(query="term-only-in-default", budget=3000, agent="shape")

    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    assert "reflib" not in collections_seen, (
        f"opt-in collection 'reflib' leaked into default search: seen={collections_seen!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_search_with_explicit_opt_in_collection_returns_it(tmp_path: Path) -> None:
    """``collections=['reflib']`` retrieves the reflib doc — opt-in
    collections are reachable by explicit name.
    """
    db_path, registry = _build_pipeline(tmp_path)
    db = _bootstrap_db(db_path)
    _seed_documents(
        db,
        docs=[
            ("sharepoint", "sp.md", "team notes opt-in-test-keyword"),
            ("reflib", "ref.md", "reference library opt-in-test-keyword"),
        ],
    )
    _seed_scope_profile_seven_in_default_one_opt_in(db, actor_id="shape")
    db.close()

    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=_make_paths(tmp_path, db_path),
    )

    result = pipeline.search(
        query="opt-in-test-keyword",
        budget=3000,
        agent="shape",
        collections=["reflib"],
    )

    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    assert "reflib" in collections_seen, (
        f"explicit ['reflib'] must retrieve the reflib doc; got collections {collections_seen!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_search_returns_agent_own_memory_in_default(tmp_path: Path) -> None:
    """``agent='shape'`` with no collections returns ``shape-memory``
    docs without explicit naming.
    """
    db_path, registry = _build_pipeline(tmp_path)
    db = _bootstrap_db(db_path)
    _seed_documents(
        db,
        docs=[
            ("shape-memory", "memo.md", "shape memory keyword own-memory-test"),
        ],
    )
    _seed_scope_profile_seven_in_default_one_opt_in(db, actor_id="shape")
    db.close()

    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=_make_paths(tmp_path, db_path),
    )

    result = pipeline.search(query="own-memory-test", budget=3000, agent="shape")

    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    assert "shape-memory" in collections_seen, (
        f"own memory must be in default search; got collections {collections_seen!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_search_does_not_return_other_agent_memory_in_default(tmp_path: Path) -> None:
    """``agent='shape'`` default search excludes builder-memory docs.

    Cross-agent memory isolation: ``builder-memory`` is in builder's
    scope, NOT shape's; default search for shape must not see those rows
    even if the keyword matches.
    """
    db_path, registry = _build_pipeline(tmp_path)
    db = _bootstrap_db(db_path)
    _seed_documents(
        db,
        docs=[
            ("shape-memory", "shape-memo.md", "leak-detection-keyword from shape"),
            ("builder-memory", "builder-memo.md", "leak-detection-keyword from builder"),
        ],
    )
    _seed_scope_profile_seven_in_default_one_opt_in(db, actor_id="shape")
    db.close()

    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=_make_paths(tmp_path, db_path),
    )

    result = pipeline.search(query="leak-detection-keyword", budget=3000, agent="shape")

    collections_seen = {getattr(getattr(row, "result", None), "collection", None) for row in result.results}
    assert "builder-memory" not in collections_seen, (
        f"cross-agent memory leak — builder-memory in shape's default search: collections={collections_seen!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_search_explicit_other_agent_memory_returns_empty_with_error_logged(
    tmp_path: Path,
) -> None:
    """``agent='shape'`` + ``collections=['builder-memory']`` returns
    empty + an F21-shaped error in the result envelope.

    Pins explicit-cross-agent rejection: there's no loophole where
    naming the collection grants access. The error must carry
    ``fix:``/``next:``/``run:`` markers (F21).
    """
    db_path, registry = _build_pipeline(tmp_path)
    db = _bootstrap_db(db_path)
    _seed_documents(
        db,
        docs=[
            ("builder-memory", "builder-memo.md", "explicit-cross-agent-keyword"),
        ],
    )
    _seed_scope_profile_seven_in_default_one_opt_in(db, actor_id="shape")
    db.close()

    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=_make_paths(tmp_path, db_path),
    )

    result = pipeline.search(
        query="explicit-cross-agent-keyword",
        budget=3000,
        agent="shape",
        collections=["builder-memory"],
    )

    assert not result.results, f"explicit out-of-scope collection must return no results; got {result.results!r}"
    error_text = str(result.error or "")
    assert "fix:" in error_text and "next:" in error_text, (
        f"F21 affordance markers missing from out-of-scope error: {error_text!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_flag_off_uses_legacy_default_collection_resolver(tmp_path: Path) -> None:
    """Feature flag OFF → SearchPipeline routes through the legacy
    :class:`DefaultCollectionResolver`.

    The legacy resolver reads ``collections.shared[].in_default`` from
    ``kairix.config.yaml`` (today's behaviour). With the flag OFF the
    factory MUST wire that resolver, not the v2 Adapter — operators get
    bit-for-bit identical behaviour to v2026.5.x.
    """
    from kairix.core.factory import build_collection_resolver

    def _off_reader(_name: str) -> bool:
        return False

    db_path, _registry = _build_pipeline(tmp_path)
    _bootstrap_db(db_path).close()

    resolver = build_collection_resolver(db_path=db_path, flag_reader=_off_reader)

    # The legacy resolver does NOT carry validate_explicit (a v2 affordance).
    # Asserting on type-shape is the cleanest factory-branch pin without
    # importing the legacy class directly.
    assert not hasattr(resolver, "validate_explicit"), (
        f"flag OFF must yield legacy DefaultCollectionResolver; got {type(resolver).__name__}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_flag_on_uses_topology_v2_collection_resolver(tmp_path: Path) -> None:
    """Feature flag ON → SearchPipeline routes through TopologyV2CollectionResolver."""
    from kairix.core.factory import build_collection_resolver

    def _on_reader(name: str) -> bool:
        return name in {
            "topology_v2_collection_resolver",
            "topology_v2_default_in_scope",
        }

    db_path, _registry = _build_pipeline(tmp_path)
    _bootstrap_db(db_path).close()

    resolver = build_collection_resolver(db_path=db_path, flag_reader=_on_reader)

    assert hasattr(resolver, "validate_explicit"), (
        f"flag ON must yield TopologyV2CollectionResolver; got {type(resolver).__name__}"
    )
