"""MM-3 integration test — search results surface per-page citation.

The connector pipeline persists ``documents.source_page`` for paged
extractor output. This test threads the data end-to-end through the
production search surface and asserts that a result row coming back to
the agent (via the MCP envelope shape) carries the page number.

Boundary chain exercised:

  documents.source_page populated (matches the connector-pipeline chunk
  writer's contract; the fixture writes directly through the public
  table surface so the test stays on public names only)
  build_search_pipeline(paths=FakePaths(db_path=tmp))   ← F47 factory composition
    → SearchPipeline.search ("quarterly outlook")
      → BM25 SQL pulls d.source_page
      → BM25Result.source_page → FusedResult.source_page
      → BudgetedResult.result.source_page
  search_output_to_envelope
    → envelope row["source_page"] == 42

Sabotage anchor: drop ``d.source_page`` from the SQL in
``_build_bm25_query`` OR strip ``source_page`` from
``search_output_to_envelope``; this test fails. Tested locally on
2026-05-22 — removing the SELECT column drops the assertion in <1s.

F47-compliant: the pipeline is constructed via
``build_search_pipeline(paths=FakePaths(...))``; no
``monkeypatch.setenv("KAIRIX_*")``, no direct pipeline construction.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema, migrate
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.integration


def _bootstrap_db(db_path: Path) -> None:
    """Create schema and seed FTS5 index for the chunk we're about to write."""
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db)
        migrate(db)
        db.commit()
    finally:
        db.close()


def _write_paged_chunk(db_path: Path, *, source_page: int) -> None:
    """Persist one chunk carrying ``source_page`` into the documents tables.

    The connector writer (under ``kairix.worker``) is private; this test
    fixture writes directly through the schema's public table surface so
    the integration test stays on public names only. The row shape
    matches the chunk-writer contract: ``documents`` carries
    ``source_page`` per SC-4 and ``content`` carries the body.
    """
    doc_text = "quarterly outlook section page-forty-two body content."
    content_hash = "hash_paged_chunk_42"
    source_uri = "src://pdf-source/quarterly.pdf"
    modified_at = "2026-05-22T00:00:00Z"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    path = f"{source_uri}#0"

    db = sqlite3.connect(str(db_path))
    try:
        db.execute(
            "INSERT OR REPLACE INTO documents "
            "(collection, path, hash, source_name, source_uri, "
            "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                "paged-collection",
                path,
                content_hash,
                "pdf-source",
                source_uri,
                modified_at,
                source_page,
                "public",
                now,
                modified_at,
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
            (content_hash, doc_text, now),
        )
        # Sync the FTS5 virtual table so the BM25 leg can find the row.
        db.execute(
            "INSERT INTO documents_fts (rowid, filepath, title, doc) "
            "SELECT d.id, d.path, COALESCE(d.title, ''), c.doc "
            "FROM documents d JOIN content c ON c.hash = d.hash "
            "WHERE d.hash = ?",
            (content_hash,),
        )
        db.commit()
    finally:
        db.close()


def _search_skip_vector_config() -> RetrievalConfig:
    """Build a retrieval config that skips the vector leg.

    Stage 5 of the search pipeline embeds the query through whichever
    provider plugin the config selects; the integration test does not
    have credentials and does not need to exercise that leg. Skipping
    it isolates the assertion on the BM25 path — which is the leg that
    actually carries ``source_page`` through the SQL. We still need a
    provider name so the factory can resolve an EmbeddingService — the
    ``FakeProvider`` from ``tests/fakes.py`` satisfies that contract
    without making a real HTTP call.
    """
    base = RetrievalConfig.defaults()
    # ``RetrievalConfig`` is frozen; ``dataclasses.replace`` keeps the
    # cache-key contract stable (resolved config == cache key).
    import dataclasses

    return dataclasses.replace(base, skip_vector=True, provider="fake")


def _fake_registry() -> FakeProviderRegistry:
    """Build a fake provider registry exposing the "fake" plugin."""
    return FakeProviderRegistry({"fake": FakeProvider(name="fake")})


def test_search_result_row_carries_source_page(tmp_path: Path) -> None:
    """A chunk written with ``source_page=42`` surfaces as ``source_page=42`` in results."""
    reset_search_pipeline_cache()
    db_path = tmp_path / "kairix.sqlite"
    _bootstrap_db(db_path)
    _write_paged_chunk(db_path, source_page=42)

    paths = FakePaths(db_path=db_path, document_root=tmp_path / "vault")
    pipeline = build_search_pipeline(
        config=_search_skip_vector_config(),
        paths=paths,
        registry=_fake_registry(),
    )

    result = pipeline.search(query="quarterly outlook", budget=3000)
    assert not result.error, f"unexpected error: {result.error!r}"
    assert result.results, "expected at least one search hit"

    # Find the row keyed on the path our writer used.
    target_path_suffix = "pdf-source/quarterly.pdf"
    matching = [
        r
        for r in result.results
        if getattr(getattr(r, "result", None), "path", "").endswith("#0")
        and target_path_suffix in getattr(getattr(r, "result", None), "path", "")
    ]
    if not matching:
        seen = [getattr(r.result, "path", "") for r in result.results]
        raise AssertionError(f"expected a hit on {target_path_suffix!r}; got: {seen}")
    inner = matching[0].result
    assert inner.source_page == 42, f"source_page lost in retrieval; got {inner.source_page!r}"


def test_search_envelope_exposes_source_page(tmp_path: Path) -> None:
    """The MCP envelope projection includes ``source_page`` on every result row.

    Sabotage proof: dropping ``"source_page": h.source_page`` from
    ``search_output_to_envelope`` makes ``"source_page" in row`` False
    and this assertion fails.
    """
    reset_search_pipeline_cache()
    db_path = tmp_path / "kairix.sqlite"
    _bootstrap_db(db_path)
    _write_paged_chunk(db_path, source_page=7)

    from kairix.use_cases.search import (
        SearchDeps,
        run_search,
        search_output_to_envelope,
    )

    paths = FakePaths(db_path=db_path, document_root=tmp_path / "vault")

    def _injected_search(*, query: str, agent: str, scope, budget: int, intent=None):
        pipeline = build_search_pipeline(
            config=_search_skip_vector_config(),
            paths=paths,
            registry=_fake_registry(),
        )
        return pipeline.search(query=query, budget=budget, scope=scope, agent=agent, intent=intent)

    deps = SearchDeps(search_fn=_injected_search)
    out = run_search("quarterly outlook", budget=3000, include_entity_card=False, deps=deps)
    envelope = search_output_to_envelope(out)

    assert envelope["results"], "expected results in the envelope"
    pages_seen = [row.get("source_page") for row in envelope["results"]]
    assert 7 in pages_seen, f"envelope did not surface source_page=7 from the indexed chunk; got pages={pages_seen}"
    # Every result row carries the key (nullable), even rows that were not paged.
    for row in envelope["results"]:
        assert "source_page" in row, "MCP envelope row must always include the source_page key"
