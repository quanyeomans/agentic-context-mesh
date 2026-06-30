"""Contract-first tests for kairix.core.search.bm25.

Probes the documented contracts of ``bm25_search``:

  - "Never raises" — all failure paths return [].
  - Score normalisation: ``abs(s) / (1 + abs(s))``.
  - Frontmatter stripping when doc starts with ``---``.
  - ``doc_repo`` injection seam: when provided, delegates to ``doc_repo.search_fts``
    and maps the raw rows into BM25Result.
  - ``date_filter_paths`` post-query filter behaviour.
  - Whitespace-only query → [] without DB access.

These tests are written against docstring claims — not the impl —
and sabotage-proven before commit.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.search.bm25 import bm25_search
from tests.fakes import FakeDocumentRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_db_with_doc(tmp_path: Path, doc_text: str, *, path: str = "doc.md") -> Path:
    """Create a single-doc SQLite DB with the given doc content.

    Mirrors the production FTS5 schema (``filepath, title, doc`` — a
    content-storing, NOT ``content=''`` contentless table) so that the
    FTS5 ``snippet()`` auxiliary function used by ``_build_bm25_query``
    behaves exactly as it does against the real index (PLA-269). A
    contentless table makes ``snippet()`` return NULL and a 2-column
    table has no column index 2 — both would silently break the snippet.
    """
    db_path = tmp_path / "single.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript(f"""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            source_page INTEGER, source_uri TEXT,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            filepath, title, doc, tokenize='porter unicode61'
        );
        INSERT INTO documents (collection, path, title, hash, active)
        VALUES ('vault', '{path}', 'Test Doc', 'h1', 1);
    """)
    db.execute("INSERT INTO content (hash, doc) VALUES ('h1', ?)", (doc_text,))
    db.execute(
        "INSERT INTO documents_fts(rowid, filepath, title, doc) "
        "SELECT d.id, d.path, d.title, c.doc FROM documents d JOIN content c ON c.hash = d.hash"
    )
    db.commit()
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# Whitespace / empty contracts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_whitespace_only_query_returns_empty_without_calling_doc_repo() -> None:
    """A whitespace-only query short-circuits to [] BEFORE the doc_repo
    branch runs. Sabotage-prove: assert the canonical fake was never called.
    """
    repo = FakeDocumentRepository(force_rows=[{"file": "x", "title": "", "snippet": "", "score": 0, "collection": ""}])
    results = bm25_search("   \t\n   ", doc_repo=repo)
    assert results == []
    assert repo.calls == [], "whitespace-only query must not reach doc_repo.search_fts"


# ---------------------------------------------------------------------------
# Score normalisation contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_score_is_normalised_to_zero_one_range(tmp_path: Path) -> None:
    """Per impl: ``score = abs(raw) / (1 + abs(raw))``. The mapped score
    must lie in [0, 1).
    """
    db_path = _create_db_with_doc(tmp_path, "kairix knowledge platform")
    results = bm25_search("kairix knowledge", db_path=db_path)
    assert results
    for r in results:
        assert 0.0 <= r["score"] < 1.0


# ---------------------------------------------------------------------------
# Snippet = matched region, not a fixed chunk prefix (PLA-269)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_snippet_is_centred_on_the_match_not_the_chunk_prefix(tmp_path: Path) -> None:
    """PLA-269: when the match term appears LATE in a long chunk, the
    snippet is the window AROUND the match — not a fixed prefix of the
    chunk opening — and the truncated leading edge carries the ``…`` marker.

    Sabotage proof: under the old ``doc_text[:300]`` prefix slice the term
    ``zelkova`` (absent from the first 300 chars — asserted below) never
    appears in the snippet, so ``"zelkova" in snippet`` would fail.
    """
    prefix = "Opening filler about unrelated background material. " * 12
    tail = "The pivotal keyword zelkova surfaces only here near the chunk tail."
    doc = prefix + tail
    # Guard: the fixture must put the match outside the old prefix window,
    # otherwise the test could not tell the new behaviour from the old.
    assert "zelkova" not in doc[:300]
    db_path = _create_db_with_doc(tmp_path, doc)

    results = bm25_search("zelkova", db_path=db_path)

    assert results
    snippet = results[0]["snippet"]
    assert "zelkova" in snippet, "snippet must contain the matched region"
    assert " … " in snippet, "a truncated-edge snippet must carry the ellipsis marker"


@pytest.mark.unit
def test_short_chunk_snippet_returned_whole_without_ellipsis(tmp_path: Path) -> None:
    """A chunk shorter than the snippet window is returned whole — the
    match is visible and there is no truncation, so no ellipsis is added.
    """
    doc = "Plain content about kairix without any frontmatter delimiter."
    db_path = _create_db_with_doc(tmp_path, doc)
    results = bm25_search("kairix", db_path=db_path)
    assert results
    snippet = results[0]["snippet"]
    assert snippet.startswith("Plain content")
    assert " … " not in snippet


@pytest.mark.unit
def test_long_chunk_snippet_is_a_bounded_window_with_ellipsis(tmp_path: Path) -> None:
    """A chunk far longer than the snippet window yields a bounded window
    (much shorter than the full chunk) ending in the ``…`` truncation marker.

    Sabotage proof: the old prefix slice ``doc[:300]`` carried no ellipsis
    marker, so ``" … " in snippet`` distinguishes the new windowed snippet
    from the retired prefix behaviour.
    """
    long = "kairix " + ("filler text " * 200)  # >> the 32-token window
    db_path = _create_db_with_doc(tmp_path, long)
    results = bm25_search("kairix", db_path=db_path)
    assert results
    snippet = results[0]["snippet"]
    assert "kairix" in snippet
    assert " … " in snippet
    assert len(snippet) < len(long), "snippet must be a window, not the full chunk"


# ---------------------------------------------------------------------------
# doc_repo injection seam contracts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_doc_repo_is_used_when_provided_skipping_direct_sql() -> None:
    """When doc_repo is provided, the function delegates to ``search_fts``
    and never touches a real DB. Confirmed by passing a nonexistent db_path
    that would fail open if the SQL branch ran.
    """
    repo = FakeDocumentRepository(
        force_rows=[{"file": "a.md", "title": "A", "snippet": "snippet A", "score": 0.5, "collection": "vault"}]
    )
    results = bm25_search(
        "anything",
        doc_repo=repo,
        db_path=Path("/nonexistent/should-not-be-touched.sqlite"),
    )
    assert len(repo.calls) == 1, "doc_repo.search_fts should have been called exactly once"
    assert results[0]["file"] == "a.md"


@pytest.mark.unit
def test_doc_repo_falls_back_to_path_key_when_file_absent() -> None:
    """Per impl: ``r.get("file", r.get("path", ""))`` — repos that
    return ``path`` instead of ``file`` must still produce a usable result.
    """
    repo = FakeDocumentRepository(
        force_rows=[{"path": "from-path-key.md", "title": "T", "snippet": "S", "score": 0.1, "collection": "c"}]
    )
    results = bm25_search("q", doc_repo=repo)
    assert len(results) == 1
    assert results[0]["file"] == "from-path-key.md"


@pytest.mark.unit
def test_doc_repo_falls_back_to_content_for_snippet_when_snippet_absent() -> None:
    """Per impl: ``r.get("snippet", r.get("content", "")[:300])``."""
    long_content = "x" * 500
    repo = FakeDocumentRepository(
        force_rows=[{"file": "a.md", "title": "T", "content": long_content, "score": 0.1, "collection": "c"}]
    )
    results = bm25_search("q", doc_repo=repo)
    assert len(results) == 1
    assert len(results[0]["snippet"]) == 300


@pytest.mark.unit
def test_doc_repo_returns_empty_when_search_fts_raises() -> None:
    """The doc_repo branch swallows exceptions and returns [] (per "Never raises")."""
    repo = FakeDocumentRepository(raises=RuntimeError("repo broken"))
    results = bm25_search("q", doc_repo=repo)
    assert results == []


@pytest.mark.unit
def test_doc_repo_applies_date_filter_paths_post_query() -> None:
    """``date_filter_paths`` filters the doc_repo branch results by file path."""
    repo = FakeDocumentRepository(
        force_rows=[
            {"file": "keep.md", "title": "K", "snippet": "S", "score": 0.5, "collection": "c"},
            {"file": "drop.md", "title": "D", "snippet": "S", "score": 0.5, "collection": "c"},
        ]
    )
    results = bm25_search(
        "q",
        doc_repo=repo,
        date_filter_paths=frozenset(["keep.md"]),
    )
    assert [r["file"] for r in results] == ["keep.md"]


@pytest.mark.unit
def test_doc_repo_propagates_collections_and_limit_to_search_fts() -> None:
    """The collections list and limit kwarg should reach the repo."""
    repo = FakeDocumentRepository(force_rows=[])
    bm25_search("q", collections=["vault", "shared"], limit=3, doc_repo=repo)
    assert repo.calls[0] == ("q", ["vault", "shared"], 3)


# ---------------------------------------------------------------------------
# date_filter_paths contracts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_date_filter_paths_none_does_not_filter(tmp_path: Path) -> None:
    """date_filter_paths=None means no path filtering."""
    db_path = _create_db_with_doc(tmp_path, "kairix content", path="some/doc.md")
    results = bm25_search("kairix", db_path=db_path, date_filter_paths=None)
    assert len(results) == 1


@pytest.mark.unit
def test_date_filter_paths_excludes_results_not_in_set(tmp_path: Path) -> None:
    """When non-empty, only results whose ``file`` is in the set are kept."""
    db_path = _create_db_with_doc(tmp_path, "kairix content", path="some/doc.md")
    results = bm25_search(
        "kairix",
        db_path=db_path,
        date_filter_paths=frozenset(["totally-different-path.md"]),
    )
    assert results == []


# ---------------------------------------------------------------------------
# Never-raises guarantee on the doc_repo branch with malformed rows
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_doc_repo_branch_does_not_raise_on_partially_shaped_rows() -> None:
    """The doc_repo branch uses ``.get()`` defaults for every field, so
    a partially-shaped row (missing every documented key) must still
    yield a result rather than raise. Validates the never-raises invariant.
    """
    repo = FakeDocumentRepository(force_rows=[{}])  # totally empty row
    # The contract is just: must not raise. The shape of the result on
    # an empty row is unspecified by the docstring, but it must be a list.
    results = bm25_search("q", doc_repo=repo)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# collections=None vs collections=[] — distinct semantics (#164)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collections_none_searches_all_active_documents(tmp_path: Path) -> None:
    """``collections=None`` is the "no filter" signal — search every active
    document regardless of collection. Sabotage anchor for the second test:
    proves the underlying DB has matchable content.
    """
    db_path = _create_db_with_doc(tmp_path, "kairix content", path="some/doc.md")
    results = bm25_search("kairix", db_path=db_path, collections=None)
    assert len(results) == 1


@pytest.mark.unit
def test_collections_empty_list_returns_no_results(tmp_path: Path) -> None:
    """``collections=[]`` is the explicit "search nothing" signal — distinct
    from ``None`` (no filter).

    Closes #164: production was conflating ``[]`` with ``None`` via
    ``if collections:``, so when the resolver narrowed scope to zero
    collections the search backend silently returned global results. The
    contract is now: ``collections=[]`` returns ``[]``, no SQL hits the DB.
    """
    db_path = _create_db_with_doc(tmp_path, "kairix content", path="some/doc.md")
    # Same query that returns 1 result with collections=None.
    results = bm25_search("kairix", db_path=db_path, collections=[])
    assert results == [], (
        "collections=[] should return no results; got results from a global "
        "search — this is the #164 conflation regression."
    )


@pytest.mark.unit
def test_doc_repo_branch_also_short_circuits_on_empty_collections() -> None:
    """The doc_repo delegation path honours the same ``collections=[]``
    short-circuit as the direct-SQL path. Without this, the bug recurs at
    a different layer: doc_repo.search_fts would receive ``collections=[]``
    and might apply the same conflation.
    """
    repo = FakeDocumentRepository(
        documents=[
            {
                "path": "doc.md",
                "title": "Doc",
                "content": "kairix content",
                "collection": "vault-areas",
            }
        ]
    )
    # Sanity: with None, the row matches.
    assert len(bm25_search("kairix", doc_repo=repo, collections=None)) == 1
    # With []: zero results, and the repo's search_fts is never invoked
    # (calls list stays the same).
    calls_before = len(repo.calls)
    assert bm25_search("kairix", doc_repo=repo, collections=[]) == []
    assert len(repo.calls) == calls_before, "doc_repo.search_fts should not be called when collections is []"
