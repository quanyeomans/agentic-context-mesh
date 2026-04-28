"""Tests for kairix.search.vec_index — usearch-backed ANN vector index."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytestmark = pytest.mark.unit


def _make_test_db(tmp_path: Path, n_docs: int = 20) -> sqlite3.Connection:
    """Create a test DB with documents and content_vectors metadata."""
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, collection TEXT, path TEXT,
            title TEXT, hash TEXT, active INTEGER DEFAULT 1,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        CREATE TABLE content_vectors (
            hash TEXT NOT NULL, seq INTEGER NOT NULL,
            pos INTEGER, model TEXT, embedded_at TEXT, chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        );
        CREATE INDEX idx_documents_hash ON documents(hash);
    """)
    for i in range(n_docs):
        collection = "reference-library" if i % 2 == 0 else "vault-projects"
        db.execute(
            "INSERT INTO documents (collection, path, title, hash, active) VALUES (?,?,?,?,1)",
            (collection, f"{collection}/doc-{i}.md", f"doc-{i}", f"hash{i}"),
        )
        db.execute(
            "INSERT INTO content (hash, doc) VALUES (?,?)",
            (f"hash{i}", f"Content of document {i} about topic {i}."),
        )
        db.execute(
            "INSERT INTO content_vectors (hash, seq, model) VALUES (?,0,?)",
            (f"hash{i}", "text-embedding-3-large"),
        )
    db.commit()
    return db


@pytest.fixture()
def test_index(tmp_path: Path) -> Any:
    """Create a VectorIndex with test data."""
    from kairix.search.vec_index import VectorIndex

    db = _make_test_db(tmp_path, n_docs=20)
    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    meta_path = tmp_path / "vectors.meta.json"

    # Create random vectors for the 20 docs
    rng = np.random.default_rng(42)
    vectors = rng.random((20, 1536), dtype=np.float32)
    # Normalize for cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / norms

    idx = VectorIndex(index_path=index_path, meta_path=meta_path, db_path=db_path)
    # Build index from provided vectors
    hash_seqs = [f"hash{i}_0" for i in range(20)]
    idx.build_from_vectors(hash_seqs, vectors)
    db.close()
    return idx


class TestVectorIndex:
    def test_build_creates_index_file(self, test_index: Any, tmp_path: Path) -> None:
        assert (tmp_path / "vectors.usearch").exists()
        assert len(test_index) == 20

    def test_search_returns_k_results(self, test_index: Any) -> None:
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = test_index.search(query, k=5)
        assert len(results) == 5

    def test_search_results_sorted_by_distance(self, test_index: Any) -> None:
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = test_index.search(query, k=10)
        distances = [r["distance"] for r in results]
        assert distances == sorted(distances)

    def test_collection_filter_excludes_non_matching(self, test_index: Any) -> None:
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = test_index.search(query, k=10, collections=["reference-library"])
        for r in results:
            assert r["collection"] == "reference-library"

    def test_collection_filter_returns_fewer_results(self, test_index: Any) -> None:
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        all_results = test_index.search(query, k=20)
        filtered = test_index.search(query, k=20, collections=["reference-library"])
        assert len(filtered) <= len(all_results)

    def test_save_and_load(self, test_index: Any, tmp_path: Path) -> None:
        from kairix.search.vec_index import VectorIndex

        # Save is done in build_from_vectors
        # Load fresh instance
        loaded = VectorIndex(
            index_path=tmp_path / "vectors.usearch",
            meta_path=tmp_path / "vectors.meta.json",
            db_path=tmp_path / "index.sqlite",
        )
        loaded.load()
        assert len(loaded) == 20

        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = loaded.search(query, k=5)
        assert len(results) == 5

    def test_add_vectors_incremental(self, test_index: Any) -> None:
        rng = np.random.default_rng(123)
        new_vec = rng.random(1536).astype(np.float32)
        new_vec /= np.linalg.norm(new_vec)
        count = test_index.add_vectors(["newhash_0"], [new_vec])
        assert count == 1
        assert len(test_index) == 21

    def test_empty_index_returns_empty(self, tmp_path: Path) -> None:
        from kairix.search.vec_index import VectorIndex

        idx = VectorIndex(
            index_path=tmp_path / "empty.usearch",
            meta_path=tmp_path / "empty.meta.json",
            db_path=tmp_path / "empty.sqlite",
        )
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        results = idx.search(query, k=5)
        assert results == []

    def test_search_results_have_required_fields(self, test_index: Any) -> None:
        """Each search result must have path, title, snippet, collection, distance."""
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = test_index.search(query, k=5)
        for r in results:
            assert "path" in r, "result missing 'path'"
            assert "title" in r, "result missing 'title'"
            assert "snippet" in r, "result missing 'snippet'"
            assert "collection" in r, "result missing 'collection'"
            assert "distance" in r, "result missing 'distance'"
            assert isinstance(r["distance"], float)

    def test_add_vectors_updates_existing_doc(self, test_index: Any) -> None:
        """Adding a vector for an existing document's hash_seq makes it searchable."""
        # Use hash0_0 which exists in the DB. Replace its vector with a known value.
        target = np.ones(1536, dtype=np.float32)
        target /= np.linalg.norm(target)
        test_index.add_vectors(["hash0_0"], [target])

        # Search with that vector — should find hash0's document
        results = test_index.search(target, k=1)
        assert len(results) == 1
        assert results[0]["distance"] < 0.01  # near-zero distance

    def test_multiple_collection_filter(self, test_index: Any) -> None:
        """Filtering by multiple collections returns docs from both."""
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = test_index.search(query, k=20, collections=["reference-library", "vault-projects"])
        collections = {r["collection"] for r in results}
        assert collections <= {"reference-library", "vault-projects"}

    def test_nonexistent_collection_returns_empty(self, test_index: Any) -> None:
        """Filtering by a collection with no docs returns empty results."""
        rng = np.random.default_rng(99)
        query = rng.random(1536).astype(np.float32)
        query /= np.linalg.norm(query)
        results = test_index.search(query, k=10, collections=["nonexistent"])
        assert results == []
