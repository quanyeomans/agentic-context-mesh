"""E2E regression for the production bug seen 2026-05-31 — the embed
pipeline silently no-ops the vec-index write path when the on-disk
``vectors.usearch`` is corrupt.

What happened in production:
  - vectors.usearch on disk was corrupt (partial write from an earlier crash).
  - Operator ran ``kairix embed embed --force --parallel 1``.
  - ``_open_usearch_index()`` tried to load the corrupt file, hit the
    ``ValueError: Not a dense USearch index!`` from usearch, caught it
    in a broad ``except``, and returned ``None``.
  - ``_maybe_clear_vec_index_for_force(force=True, vec_index=None)``
    guarded on ``vec_index is not None`` and skipped clear().
  - Whole embed loop ran with ``vec_index=None`` — every batch's
    ``_add_batch_to_vec_index(None, ...)`` returned at the
    ``if vec_index is None`` guard, every ``_save_index_checkpoint(None)``
    did the same. SQLite ``content_vectors`` advanced; the on-disk
    usearch file stayed corrupt for the entire 2.18M-chunk, ~6h run.
  - Operator paid for Azure embedding calls that never produced a usable
    on-disk index.

The fix is structural: ``_open_usearch_index()`` must NOT return None
on a load failure under ``--force``. Instead it must clear the corrupt
file and return a fresh empty index that the run can write into.

These tests exercise the FULL real path — real ``VectorIndex``, real
on-disk SQLite, real ``EmbeddingCache``, a counting fake for the Azure
embedder. The bug reproduces because the integration tests in
``test_embed_uses_cache.py`` all inject ``open_usearch_index=lambda: None``
which sidesteps the very code path that broke.

Carries ``@pytest.mark.e2e`` per F48 — runs under ``pytest -m e2e`` in
CI Stage 4.5.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db import EMBED_VECTOR_DIMS
from kairix.core.db.schema import create_schema
from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed
from kairix.core.embed.embedding_cache import EmbeddingCache
from kairix.core.embed.embed import open_usearch_index_for_paths
from kairix.core.search.vec_index import VectorIndex

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


# ── Fixtures + helpers ──────────────────────────────────────────────────────


class _CountingEmbedder:
    """Deterministic embedder that counts calls for assertion."""

    def __init__(self) -> None:
        self.call_count = 0
        self.text_count = 0

    def __call__(
        self,
        texts: list[str],
        _api_key: str,
        _endpoint: str,
        _deployment: str,
        dims: int,
        **_kwargs: Any,
    ) -> list[list[float]]:
        self.call_count += 1
        self.text_count += len(texts)
        # Deterministic vector per text — same input always produces
        # the same output (so cache round-trip equality holds).
        return [[float(hash(t) % 1000) / 1000.0] * dims for t in texts]


def _seed_corpus(db_path: Path, n_docs: int = 4) -> None:
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    for i in range(n_docs):
        body = f"document {i} body text " * 50
        db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (f"h{i}", body))
        db.execute(
            "INSERT INTO documents (hash, path, active, collection) VALUES (?, ?, 1, ?)",
            (f"h{i}", f"docs/doc{i}.md", "test"),
        )
    db.commit()
    db.close()


def _write_corrupt_vec_index(index_path: Path) -> None:
    """Mirror the production failure: a vectors.usearch file that exists
    on disk but isn't a valid usearch header.

    1.1KB of arbitrary bytes — usearch's ``Index.restore`` will raise
    ``ValueError: Not a dense USearch index!`` on this content,
    reproducing the exact error from the prod log.
    """
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(b"NOT A USEARCH INDEX" + b"\x00" * 1024)


def _real_open_usearch_index_for(db_path: Path) -> Any:
    """Production-shaped open_usearch_index — opens REAL VectorIndex at
    the conventional ``<db_dir>/vectors.usearch`` path.

    Delegates to ``open_usearch_index_for_paths`` — the same function
    the production ``_open_usearch_index()`` wraps with env-derived
    paths. Tests exercise this directly so the corrupt-recovery code
    path is covered.
    """
    return open_usearch_index_for_paths(
        index_path=db_path.parent / "vectors.usearch",
        meta_path=db_path.parent / "vectors.meta.json",
        db_path=db_path,
    )


def _make_deps_with_real_vec_index(
    embedder: _CountingEmbedder,
    cache: EmbeddingCache,
    db_path: Path,
) -> EmbedDependencies:
    return EmbedDependencies(
        get_azure_config=lambda: ("k", "https://endpoint.example", "test-model"),
        preflight_check=lambda *_a, **_kw: EMBED_VECTOR_DIMS,
        embed_batch=embedder,
        open_usearch_index=lambda: _real_open_usearch_index_for(db_path),
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
        open_embedding_cache=lambda: cache,
    )


# ── The bug-reproducing tests ────────────────────────────────────────────────


def test_force_embed_with_corrupt_existing_index_writes_new_valid_index(tmp_path: Path) -> None:
    """The production bug: corrupt vectors.usearch + --force → silent no-op.

    Sets up the exact failure shape the 2026-05-31 prod run hit:
      - Corrupt vectors.usearch on disk
      - --force flag passed
      - Cache is empty (first-ever run from corrupt state)

    Expected after fix:
      - Run completes with `embedded > 0`
      - vectors.usearch on disk is a VALID usearch index
      - Index contains the embedded vectors (count > 0)

    Before fix: vectors.usearch stays as the corrupt input bytes.
    """
    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"

    _seed_corpus(db_path)
    _write_corrupt_vec_index(index_path)
    corrupt_bytes = index_path.read_bytes()

    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps_with_real_vec_index(embedder, cache, db_path)

    db = sqlite3.connect(str(db_path))
    try:
        result = run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()
        cache.close()

    assert result["embedded"] == 4, f"expected 4 chunks embedded, got {result}"

    # The on-disk file must have been overwritten with a valid index.
    new_bytes = index_path.read_bytes()
    assert new_bytes != corrupt_bytes, (
        "vectors.usearch was not overwritten — the embed pipeline silently no-op'd "
        "the vec-index write path. This is the production bug."
    )

    # The new file must load cleanly into a fresh VectorIndex.
    reader = VectorIndex(
        index_path=index_path,
        meta_path=tmp_path / "vectors.meta.json",
        db_path=db_path,
        read_only=False,
    )
    loaded_count = reader.load()
    assert loaded_count > 0, f"reloaded vec_index has 0 vectors; embed never persisted: {result}"


def test_force_embed_with_corrupt_index_emits_actionable_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt-on-load event must surface a warning operators can act on.

    Silent failure is the worst shape — operators don't know they've
    lost the index. After the fix, a warning carrying 'recreating' or
    'corrupt' lets the operator confirm the recovery path fired.
    """
    import logging

    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"

    _seed_corpus(db_path)
    _write_corrupt_vec_index(index_path)

    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps_with_real_vec_index(embedder, cache, db_path)

    with caplog.at_level(logging.WARNING, logger="kairix.core.search.vec_index"):
        db = sqlite3.connect(str(db_path))
        try:
            run_embed(db=db, force=True, batch_size=100, deps=deps)
        finally:
            db.close()
            cache.close()

    messages = [r.getMessage() for r in caplog.records]
    assert any("corrupt" in m.lower() or "recreating" in m.lower() for m in messages), (
        f"expected an actionable WARNING about corrupt-then-recreate; got {messages}"
    )


def test_force_embed_recovers_from_tmp_file_left_by_previous_crash(tmp_path: Path) -> None:
    """A .tmp file from a crash mid-save must be cleaned up by --force.

    Production crash scenario: a previous embed crashed between
    fsync(.tmp) and rename(.tmp → canonical). The next --force run
    must not be confused by the orphan .tmp file.
    """
    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    tmp_index_path = tmp_path / "vectors.usearch.tmp"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"

    _seed_corpus(db_path)
    # No canonical file — only an orphan .tmp from a crashed prior run.
    tmp_index_path.write_bytes(b"GARBAGE TMP FROM CRASHED RUN" + b"\x00" * 256)

    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps_with_real_vec_index(embedder, cache, db_path)

    db = sqlite3.connect(str(db_path))
    try:
        result = run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()
        cache.close()

    assert result["embedded"] == 4
    # Canonical file must exist and be valid; .tmp must be cleaned up.
    assert index_path.exists(), "canonical vectors.usearch was not written"
    assert not tmp_index_path.exists(), f"orphan .tmp file from crashed prior run not cleaned up: {tmp_index_path}"


def test_incremental_embed_with_corrupt_existing_index_auto_recovers_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Outside --force mode, a corrupt index must auto-recover AND warn.

    The pre-fix behaviour was the production bug: corrupt file → load
    failure → silent ``vec_index=None`` for the rest of the run → no
    vector writes, no warning. Operators learnt about it via the next
    recall canary.

    The post-fix behaviour: corrupt file → load_or_recreate detects
    corruption → deletes the corrupt files → returns fresh empty
    index → run populates it. A WARNING log carrying "corrupt" /
    "recreating" surfaces the recovery so operators can audit it.
    Auto-recovery is the right shape because the alternative is
    broken-search-until-operator-notices — and `--force` is just an
    operator-unfriendly gate when the alternative is a working index.
    """
    import logging

    db_path = tmp_path / "index.sqlite"
    index_path = tmp_path / "vectors.usearch"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"

    _seed_corpus(db_path)
    _write_corrupt_vec_index(index_path)
    corrupt_bytes = index_path.read_bytes()

    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps_with_real_vec_index(embedder, cache, db_path)

    with caplog.at_level(logging.WARNING, logger="kairix.core.search.vec_index"):
        db = sqlite3.connect(str(db_path))
        try:
            result = run_embed(db=db, force=False, batch_size=100, deps=deps)
        finally:
            db.close()
            cache.close()

    # Incremental mode embedded the corpus + persisted a fresh index.
    assert result["embedded"] == 4
    assert index_path.read_bytes() != corrupt_bytes, "corrupt index must have been overwritten with valid one"

    # Recovery surfaced an actionable warning so operators can audit it.
    messages = [r.getMessage() for r in caplog.records]
    assert any("corrupt" in m.lower() or "recreating" in m.lower() for m in messages), (
        f"auto-recovery must emit a WARNING so operators see it; got {messages}"
    )

    # The new file is a valid usearch index.
    reader = VectorIndex(
        index_path=index_path,
        meta_path=tmp_path / "vectors.meta.json",
        db_path=db_path,
        read_only=False,
    )
    loaded_count = reader.load()
    assert loaded_count > 0
