"""#475 — reference_library.index mode + user-docs-first embed ordering.

Driven through the public ``run_embed`` boundary (F5-clean). The fake
corpus seeds a reference-library document BEFORE a user document so
SQLite's natural rowid order would yield reflib-first — every ordering
assertion below fails if the ``ORDER BY`` in the gather is removed.

Contracts pinned:

  * eager (default): both collections embed in one run, user docs first
  * lazy: a run with pending user docs embeds ONLY user docs; the next
    run picks up the deferred reference-library docs (multi-run advance)
  * skip: reference-library docs never embed, run after run
  * legacy fixtures without a ``documents.collection`` column keep
    working (PRAGMA guard — everything is treated as user documents)
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed

pytestmark = pytest.mark.unit

_USER_TEXT = "User onboarding notes for agent-alpha. " * 5
_REFLIB_TEXT = "Bundled reference library article. " * 5


def _seed_schema(db: sqlite3.Connection, *, with_collection: bool = True) -> None:
    collection_col = "collection TEXT, " if with_collection else ""
    db.execute(
        f"CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, {collection_col}"
        "active INTEGER DEFAULT 1, source_modified_at TEXT)"
    )
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )


def _insert_doc(db: sqlite3.Connection, *, hash_: str, body: str, path: str, collection: str) -> None:
    db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (hash_, body))
    db.execute(
        "INSERT INTO documents (hash, path, collection, active) VALUES (?, ?, ?, 1)",
        (hash_, path, collection),
    )


def _seed_reflib_then_user(db: sqlite3.Connection) -> None:
    """Reflib row first so rowid order (no ORDER BY) would embed it first."""
    _insert_doc(
        db,
        hash_="h_reflib",
        body=_REFLIB_TEXT,
        path="reference-library/article.md",
        collection="reference-library",
    )
    _insert_doc(db, hash_="h_user", body=_USER_TEXT, path="notes/user-doc.md", collection="default")
    db.commit()


def _build_deps(mode: str, calls: list[list[str]]) -> EmbedDependencies:
    def _embed(texts: list[str], *_a: object, **_kw: object) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1] * 1536 for _ in texts]

    return EmbedDependencies(
        get_azure_config=lambda: ("key", "https://ep.example", "deploy"),
        preflight_check=lambda *_a, **_kw: 1536,
        migrate_content_vectors=lambda _db: None,
        open_usearch_index=lambda: None,
        get_document_root=lambda: None,
        embed_batch=_embed,
        get_reflib_index_mode=lambda: mode,
        rate_limit_sleep=lambda _s: None,
    )


def _embedded_hashes(db: sqlite3.Connection) -> set[str]:
    # F63-bounded: two-document test fixture.
    return {row[0] for row in db.execute("SELECT DISTINCT hash FROM content_vectors").fetchall()}


def test_eager_embeds_user_docs_before_reference_library() -> None:
    """Default mode: both embed in one run, user-doc chunks written first.

    batch_size=1 → one provider call per chunk, so the call sequence IS
    the write sequence. Sabotage proof: removing the ORDER BY in
    ``_gather_pending_chunks`` restores rowid order (reflib first) and
    the first-call assertion fails.
    """
    db = sqlite3.connect(":memory:")
    _seed_schema(db)
    _seed_reflib_then_user(db)
    calls: list[list[str]] = []

    result = run_embed(db, batch_size=1, deps=_build_deps("eager", calls))

    assert result["embedded"] == 2
    assert _embedded_hashes(db) == {"h_user", "h_reflib"}
    # Texts under CHUNK_SIZE_CHARS pass through chunk_text verbatim.
    assert calls == [[_USER_TEXT], [_REFLIB_TEXT]], "user-doc chunk must be embedded before the reflib chunk"


def test_lazy_defers_reflib_while_user_docs_pending_then_catches_up() -> None:
    """lazy: run 1 embeds only the user doc; run 2 picks up the reflib doc.

    Sabotage proof: removing the lazy branch in
    ``_apply_reflib_index_mode`` embeds both in run 1 and the
    first-run assertion fails.
    """
    db = sqlite3.connect(":memory:")
    _seed_schema(db)
    _seed_reflib_then_user(db)
    calls: list[list[str]] = []
    deps = _build_deps("lazy", calls)

    first = run_embed(db, batch_size=10, deps=deps)
    assert first["embedded"] == 1
    assert _embedded_hashes(db) == {"h_user"}, "reflib must be deferred while user docs are pending"

    second = run_embed(db, batch_size=10, deps=deps)
    assert second["embedded"] == 1
    assert _embedded_hashes(db) == {"h_user", "h_reflib"}, "deferred reflib doc embeds once user docs are done"


def test_lazy_with_no_pending_user_docs_embeds_reflib() -> None:
    """lazy never starves the reference library: reflib-only backlog embeds."""
    db = sqlite3.connect(":memory:")
    _seed_schema(db)
    _insert_doc(
        db,
        hash_="h_reflib_only",
        body=_REFLIB_TEXT,
        path="reference-library/solo.md",
        collection="reference-library",
    )
    db.commit()
    calls: list[list[str]] = []

    result = run_embed(db, batch_size=10, deps=_build_deps("lazy", calls))

    assert result["embedded"] == 1
    assert _embedded_hashes(db) == {"h_reflib_only"}


def test_skip_never_embeds_reference_library() -> None:
    """skip: reflib docs excluded run after run; user docs unaffected.

    Sabotage proof: removing the skip branch in
    ``_apply_reflib_index_mode`` embeds the reflib doc and the
    hash-set assertion fails.
    """
    db = sqlite3.connect(":memory:")
    _seed_schema(db)
    _seed_reflib_then_user(db)
    calls: list[list[str]] = []
    deps = _build_deps("skip", calls)

    first = run_embed(db, batch_size=10, deps=deps)
    assert first["embedded"] == 1
    assert _embedded_hashes(db) == {"h_user"}

    second = run_embed(db, batch_size=10, deps=deps)
    assert second["embedded"] == 0
    assert _embedded_hashes(db) == {"h_user"}, "skip must hold across runs"


def test_skip_applies_under_force_rebuild() -> None:
    """--force + skip: vectors are cleared but the reflib doc is NOT re-embedded.

    This is the verified first-boot failure shape (#475): image-baked
    vectors mismatch the user's provider, --force clears them, and skip
    keeps the 14K-chunk bundled library out of the rebuild.
    """
    db = sqlite3.connect(":memory:")
    _seed_schema(db)
    _seed_reflib_then_user(db)
    calls: list[list[str]] = []

    result = run_embed(db, force=True, batch_size=10, deps=_build_deps("skip", calls))

    assert result["embedded"] == 1
    assert _embedded_hashes(db) == {"h_user"}


def test_legacy_schema_without_collection_column_still_embeds() -> None:
    """PRAGMA guard: documents tables without ``collection`` treat every
    row as a user document — lazy/skip filtering is inert, nothing crashes."""
    db = sqlite3.connect(":memory:")
    _seed_schema(db, with_collection=False)
    db.execute("INSERT INTO content (hash, doc) VALUES ('h_legacy', ?)", (_USER_TEXT,))
    db.execute("INSERT INTO documents (hash, path, active) VALUES ('h_legacy', 'legacy.md', 1)")
    db.commit()
    calls: list[list[str]] = []

    result = run_embed(db, batch_size=10, deps=_build_deps("lazy", calls))

    assert result["embedded"] == 1
    assert _embedded_hashes(db) == {"h_legacy"}
