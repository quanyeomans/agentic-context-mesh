"""GH #329 — chunk_date must fall back to ``documents.source_modified_at``
when the body-text extractor can't find a date.

Driven through the public ``run_embed`` boundary (F5-clean — no
``_gather_pending_chunks`` import). The contract observed: after a
run, ``content_vectors.chunk_date`` carries the body-derived date
when present, the envelope timestamp otherwise.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed

pytestmark = pytest.mark.unit


def _seed_minimal(db: sqlite3.Connection) -> None:
    """Minimal schema the embed gather path reads — including the new
    GH #329 ``source_modified_at`` column."""
    db.execute(
        "CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1, source_modified_at TEXT)"
    )
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )


def _insert_doc(db: sqlite3.Connection, hash_: str, body: str, path: str, source_modified_at: str | None) -> None:
    db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (hash_, body))
    db.execute(
        "INSERT INTO documents (hash, path, active, source_modified_at) VALUES (?, ?, 1, ?)",
        (hash_, path, source_modified_at),
    )


def _build_deps() -> EmbedDependencies:
    return EmbedDependencies(
        get_azure_config=lambda: ("key", "https://ep.com", "deploy"),
        preflight_check=lambda *_a, **_kw: 1536,
        migrate_content_vectors=lambda _db: None,
        open_usearch_index=lambda: None,
        get_document_root=lambda: None,
        embed_batch=lambda texts, *_a, **_kw: [[0.1] * 1536 for _ in texts],
    )


def test_chunk_date_uses_extracted_frontmatter_when_present() -> None:
    """Body has Obsidian-style frontmatter → that date wins over envelope.

    Sabotage proof: change the fallback in `_gather_pending_chunks` to
    drop the body-extracted date; the assertion fails because content_vectors
    carries the envelope's 2026-01-01 instead of the frontmatter's 2024-03-15.
    """
    db = sqlite3.connect(":memory:")
    _seed_minimal(db)
    _insert_doc(
        db,
        hash_="h_obsidian",
        body="---\ndate: 2024-03-15\n---\n\nObsidian note body content. " * 5,
        path="01-Projects/note-alpha.md",
        source_modified_at="2026-01-01T00:00:00Z",
    )
    db.commit()

    run_embed(db, batch_size=10, deps=_build_deps())

    rows = db.execute("SELECT chunk_date FROM content_vectors WHERE hash = 'h_obsidian'").fetchall()
    assert rows, "expected at least one content_vectors row for the seeded doc"
    assert rows[0][0] == "2024-03-15", f"body-extracted date should win; got {rows[0][0]!r}"


def test_chunk_date_falls_back_to_source_modified_at_when_body_has_no_date() -> None:
    """GH #329 — when extract_chunk_date returns None (no frontmatter,
    no parseable date in path), chunk_date inherits the envelope's
    ``source_modified_at`` (the SharePoint lastModifiedDateTime path).

    Sabotage proof: remove the ``or source_modified_at`` clause in
    `_gather_pending_chunks`; chunk_date stays None and the assertion fails.
    """
    db = sqlite3.connect(":memory:")
    _seed_minimal(db)
    _insert_doc(
        db,
        hash_="h_sharepoint",
        body="Some SharePoint content. No frontmatter. No date in body text. " * 5,
        path="sharepoint/sales/quarterly-review.docx",
        source_modified_at="2026-05-15T10:30:00Z",
    )
    db.commit()

    run_embed(db, batch_size=10, deps=_build_deps())

    rows = db.execute("SELECT chunk_date FROM content_vectors WHERE hash = 'h_sharepoint'").fetchall()
    assert rows, "expected at least one content_vectors row for the seeded doc"
    assert rows[0][0] == "2026-05-15T10:30:00Z", f"envelope timestamp should fall through; got {rows[0][0]!r}"


def test_chunk_date_remains_none_when_neither_body_nor_envelope_has_date() -> None:
    """If source_modified_at is also NULL, chunk_date stays None — degradation
    documented, not silently fabricated.
    """
    db = sqlite3.connect(":memory:")
    _seed_minimal(db)
    _insert_doc(
        db,
        hash_="h_dateless",
        body="Some content with no date anywhere. " * 5,
        path="no-date/file.txt",
        source_modified_at=None,
    )
    db.commit()

    run_embed(db, batch_size=10, deps=_build_deps())

    rows = db.execute("SELECT chunk_date FROM content_vectors WHERE hash = 'h_dateless'").fetchall()
    assert rows
    assert rows[0][0] is None


def _unused() -> Any:
    """Suppress unused-import on Any (typing-only)."""
    return None
