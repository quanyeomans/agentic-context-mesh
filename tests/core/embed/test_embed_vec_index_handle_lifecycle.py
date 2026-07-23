"""GH #375 — once-per-run vec_index handle lifecycle contract.

These tests pin the embed-loop's :class:`VecIndexBatchWriter` lifecycle:

  1. The writer is entered exactly once per ``run_embed`` invocation
     and exited exactly once, regardless of batch count. A future
     refactor that moves the open inside the batch loop (the regression
     #375 guards against) breaks ``test_writer_opened_once_per_run``
     loudly — ``enter_count`` grows to ``n_batches``.

  2. Incremental ``save()`` cadence is driven by
     ``save_every_n_batches`` (constructor-injectable, no env var).
     The default of 10 is preserved for production; tests pass 2 so
     the cadence + final-save symmetry is observable in a 5-batch
     run.

  3. Zero-batch runs still enter+exit the writer (setup/teardown
     symmetry) but issue zero ``save()`` calls — the final save is
     gated on ``batches_added > 0`` so we don't write a 10 GB+ file
     for a no-op invocation.

Test discipline:

* No ``@patch`` / no ``monkeypatch`` on kairix internals (F1).
* No env-var ``setenv`` (F2).
* Marker ``pytestmark = pytest.mark.unit`` carried on the module (F8).
* Pipeline composed via ``run_embed`` with ``deps=EmbedDependencies(...)``
  and ``vec_writer=`` injected directly — no internal-symbol patch.
* Each test ships a sabotage-proof in its docstring; the executed
  mutate / fail / restore pytest output is captured in the
  per-commit notes attached to this PR (cherry-pick body).
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import VecIndexBatchWriter, run_embed

pytestmark = pytest.mark.unit


# ── Fakes (canonical-style, inline) ───────────────────────────────────────────


class _FakeVecIndex:
    """Bare minimum vec_index stand-in for the batch writer to wrap.

    Provides the surface :class:`VecIndexBatchWriter` actually exercises:
    ``add_vectors(keys, vectors)``, ``save()``, ``__len__``. No usearch,
    no on-disk file. Counts every call so the writer-level cadence can
    be asserted from outside.
    """

    def __init__(self) -> None:
        self.add_calls = 0
        self.save_calls = 0
        self._n_vectors = 0

    def add_vectors(self, keys: list[str], vectors: list[list[float]]) -> int:
        self.add_calls += 1
        self._n_vectors += len(keys)
        return len(keys)

    def save(self) -> None:
        self.save_calls += 1

    def __len__(self) -> int:
        return self._n_vectors


class _LifecycleCountingWriter:
    """Drop-in replacement for :class:`VecIndexBatchWriter` that records
    ``__enter__`` / ``__exit__`` / ``save`` / ``add_batch`` call counts.

    Passed to ``run_embed(vec_writer=...)`` so the lifecycle test
    asserts directly on the writer rather than reaching into the
    underlying vec_index. This is the F1-clean substitution: the
    production code calls ``with vec_writer:`` and ``vec_writer.add_batch(...)``
    on whatever object is wired in; the fake just records.

    ``save_every_n_batches`` mirrors the real writer's contract so the
    cadence-firing test can swap fakes if needed; here we count the
    save calls directly.
    """

    def __init__(self, save_every_n_batches: int = 10) -> None:
        self.save_every_n_batches = save_every_n_batches
        self.enter_count = 0
        self.exit_count = 0
        self.save_count = 0
        self.add_batch_count = 0
        self._batches_added = 0

    def __enter__(self) -> _LifecycleCountingWriter:
        self.enter_count += 1
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.exit_count += 1
        # Mirror real writer: final save iff at least one batch was added.
        if self._batches_added > 0:
            self.save_count += 1

    def add_batch(
        self,
        matched: list[dict[str, Any]],
        vectors: list[list[float]],
        batch_idx: int,
    ) -> None:
        del matched, vectors, batch_idx  # contract only — we don't validate payloads here
        self.add_batch_count += 1
        self._batches_added += 1
        if self._batches_added % self.save_every_n_batches == 0:
            self.save_count += 1


# ── DB fixture helpers ────────────────────────────────────────────────────────


def _seed_documents(db: sqlite3.Connection, n_docs: int) -> None:
    """Stand up the minimum embed-pipeline schema and seed ``n_docs`` docs.

    Each document body is below ``CHUNK_SIZE_CHARS`` (3 600) so it
    produces exactly one chunk — keeps the batch arithmetic obvious
    (batch count = ceil(n_docs / batch_size)).
    """
    db.execute("CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1)")
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT,"
        " PRIMARY KEY (hash, seq))"
    )
    body = "short body content"
    for i in range(n_docs):
        h = f"h{i:05d}"
        db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (h, body))
        db.execute(
            "INSERT INTO documents (hash, path, active) VALUES (?, ?, 1)",
            (h, f"docs/agent-alpha/note{i}.md"),
        )
    db.commit()


def _seed_duplicate_hash_documents(db: sqlite3.Connection) -> None:
    """Seed two active document rows that point at the same content hash."""
    db.execute("CREATE TABLE documents (hash TEXT, path TEXT, active INTEGER DEFAULT 1)")
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT,"
        " PRIMARY KEY (hash, seq))"
    )
    db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", ("shared-hash", "short duplicate body"))
    db.execute("INSERT INTO documents (hash, path, active) VALUES (?, ?, 1)", ("shared-hash", "docs/a.md"))
    db.execute("INSERT INTO documents (hash, path, active) VALUES (?, ?, 1)", ("shared-hash", "docs/b.md"))
    db.commit()


def _build_deps(dims: int = 1536) -> EmbedDependencies:
    """Build an ``EmbedDependencies`` that fakes every external call.

    ``open_usearch_index=lambda: None`` so the lifecycle test never
    touches the real usearch path; the writer the test passes via
    ``vec_writer=`` is the single source of truth for the lifecycle
    assertions. The fake embed_batch returns deterministic vectors —
    the lifecycle is invariant to vector content.
    """
    return EmbedDependencies(
        get_azure_config=lambda: ("fake-key", "https://fake.endpoint", "fake-model"),
        preflight_check=lambda *_a, **_kw: dims,
        embed_batch=lambda texts, *_a, **_kw: [[0.01] * dims for _ in texts],
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_writer_opened_once_per_run() -> None:
    """A 5-batch ``run_embed`` opens + closes the writer exactly once.

    Drives the once-per-run invariant the production code commits to
    via the ``with vec_writer:`` block in ``run_embed``. If a future
    refactor moves the ``with`` block into the batch loop (the #375
    regression shape) ``enter_count`` becomes 5 and the test fails
    loudly.

    Sabotage-proven: mutate ``run_embed`` so the ``with vec_writer:``
    line sits inside the ``for batch_idx, batch in batched(...)``
    loop (i.e. enter / exit on every batch). Pytest then reports::

        AssertionError: assert 5 == 1
        Where 5 = writer.enter_count

    Restored after; current code passes.
    """
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=5)
    deps = _build_deps()
    writer = _LifecycleCountingWriter()

    result = run_embed(db, batch_size=1, deps=deps, vec_writer=writer)

    assert result["embedded"] == 5, f"expected 5 chunks embedded, got {result}"
    assert writer.enter_count == 1, (
        f"writer was entered {writer.enter_count} times; expected 1. "
        "fix: the embed loop is re-opening the vec_index on every batch "
        "— the 50x throughput regression #375 guards against. "
        "next: move the ``with vec_writer:`` block back outside the "
        "batch loop in ``run_embed``. "
        "run: pytest tests/core/embed/test_embed_vec_index_handle_lifecycle.py -xvs"
    )
    assert writer.exit_count == 1, f"writer exit_count={writer.exit_count}, expected 1"
    assert writer.add_batch_count == 5, f"expected 5 add_batch calls (one per batch); got {writer.add_batch_count}"


def test_run_embed_deduplicates_duplicate_document_hashes_before_vec_index() -> None:
    """Duplicate active ``documents`` rows must not inflate the USEARCH index.

    The live VM had duplicate active rows for the same content hash. The force
    embed loop selected both rows, SQLite collapsed them to one
    ``content_vectors`` row via ``PRIMARY KEY(hash, seq)``, but USEARCH received
    both vectors and preflight reported ``usearch > content_vectors`` drift.

    Sabotage: remove chunk de-duplication from ``_gather_pending_chunks`` and
    this test fails with ``embedded=2`` / ``len(vec_index)=2`` while SQLite has
    only one row.
    """
    db = sqlite3.connect(":memory:")
    _seed_duplicate_hash_documents(db)
    fake_vec_index = _FakeVecIndex()

    result = run_embed(
        db,
        force=True,
        batch_size=10,
        deps=_build_deps(),
        vec_writer=VecIndexBatchWriter(fake_vec_index),
    )

    row = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()
    sqlite_vectors = int(row[0]) if row else 0
    assert result["embedded"] == 1
    assert sqlite_vectors == 1
    assert len(fake_vec_index) == sqlite_vectors


def test_incremental_save_fires_every_n_batches() -> None:
    """With ``save_every_n_batches=2`` + 5 batches, ``save()`` fires 3 times.

    Cadence breakdown (production :class:`VecIndexBatchWriter`):

      batch 1 → batches_added=1, no save
      batch 2 → batches_added=2, incremental save (1st)
      batch 3 → batches_added=3, no save
      batch 4 → batches_added=4, incremental save (2nd)
      batch 5 → batches_added=5, no save
      __exit__ → batches_added > 0 → final save (3rd)

    Total saves = 3. This pins the contract that incremental saves
    happen at the configured cadence AND the final save fires
    regardless of where the last batch lands relative to the cadence.

    The test wires the **real** :class:`VecIndexBatchWriter` around a
    :class:`_FakeVecIndex` and counts ``save()`` calls on the
    underlying fake — so a regression that drops the cadence check in
    production fails this test loudly, while the lifecycle fake
    above isolates ``__enter__`` / ``__exit__`` accounting.

    Sabotage-proven: mutate :meth:`VecIndexBatchWriter.add_batch` to
    drop the cadence check (delete ``if self._batches_added %
    self._save_every_n_batches == 0: self._vec_index.save()``). The
    fake's ``save_calls`` drops to 1 (final save only) and the
    assertion ``save_calls == 3`` fails. Restored after.
    """
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=5)
    deps = _build_deps()
    fake_vec_index = _FakeVecIndex()
    writer = VecIndexBatchWriter(fake_vec_index, save_every_n_batches=2)

    result = run_embed(db, batch_size=1, deps=deps, vec_writer=writer)

    assert result["embedded"] == 5
    assert fake_vec_index.add_calls == 5, (
        f"expected 5 add_vectors calls (one per batch); got {fake_vec_index.add_calls}"
    )
    assert fake_vec_index.save_calls == 3, (
        f"expected 3 saves (2 incremental at batches 2 + 4, plus final on "
        f"__exit__); got {fake_vec_index.save_calls}. "
        "fix: verify the save-cadence threshold check + final-save guard "
        "in VecIndexBatchWriter."
    )


def test_save_count_zero_when_no_batches_processed() -> None:
    """A run with no pending chunks still enters + exits the writer,
    but issues zero ``save()`` calls.

    Symmetric lifecycle is what makes a future "did we open it?"
    assertion meaningful — every successful invocation has
    enter_count == 1 and exit_count == 1 (asserted via the lifecycle
    fake). The save-count guard prevents writing a 10 GB+ file for a
    no-op invocation; only work actually done in the run is flushed
    at exit (asserted via the real writer + fake vec_index — a
    regression in the production guard makes the fake's
    ``save_calls`` jump to 1).

    Sabotage-proven: drop the ``if self._batches_added == 0: return``
    guard from :meth:`VecIndexBatchWriter.__exit__` so the final
    save always fires. The fake vec_index records save_calls=1 and
    this test fails (the underlying _vec_index.save() would fire in
    production — at production scale that's a 10 GB write for a
    zero-work run, exactly the regression #375 calls out). Restored
    after.
    """
    # ── Half 1: lifecycle fake — enter/exit symmetry ──
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=0)  # zero docs → zero pending chunks
    deps = _build_deps()
    lifecycle_writer = _LifecycleCountingWriter(save_every_n_batches=2)

    result = run_embed(db, batch_size=1, deps=deps, vec_writer=lifecycle_writer)

    assert result["embedded"] == 0
    assert lifecycle_writer.enter_count == 1, (
        f"writer enter_count={lifecycle_writer.enter_count}; expected 1 "
        "(setup/teardown symmetry — every run enters + exits the writer "
        "regardless of work)"
    )
    assert lifecycle_writer.exit_count == 1, f"writer exit_count={lifecycle_writer.exit_count}; expected 1"
    assert lifecycle_writer.add_batch_count == 0, "no batches → no add_batch calls"

    # ── Half 2: real writer + fake vec_index — final-save guard ──
    db2 = sqlite3.connect(":memory:")
    _seed_documents(db2, n_docs=0)
    deps2 = _build_deps()
    fake_vec_index = _FakeVecIndex()
    real_writer = VecIndexBatchWriter(fake_vec_index, save_every_n_batches=2)

    result2 = run_embed(db2, batch_size=1, deps=deps2, vec_writer=real_writer)

    assert result2["embedded"] == 0
    assert fake_vec_index.add_calls == 0, "no batches → no add_vectors calls"
    assert fake_vec_index.save_calls == 0, (
        f"expected 0 saves on a no-batch run; got {fake_vec_index.save_calls}. "
        "fix: the final-save guard ``if self._batches_added == 0: return`` "
        "in VecIndexBatchWriter.__exit__ is missing or broken — a no-batch "
        "run would write the full vec_index file (10 GB+ at prod scale) for "
        "no observable benefit."
    )


# ── Writer-level direct contract test (no run_embed) ──────────────────────────


def test_vec_index_batch_writer_save_every_n_batches_validates() -> None:
    """``save_every_n_batches < 1`` is rejected at construction time with
    an F21-shaped affordance.

    Drives the constructor guard so operators don't accidentally
    disable incremental saves by passing 0 (which would defer every
    save to ``__exit__``, losing bounded write-amplification on a
    crash).

    Sabotage-proven: remove the ``if save_every_n_batches < 1: raise``
    guard from :class:`VecIndexBatchWriter.__init__` and rerun.
    ``pytest.raises(ValueError)`` no longer fires and the test
    fails. Restored after.
    """
    with pytest.raises(ValueError, match="save_every_n_batches=0 out of range"):
        VecIndexBatchWriter(_FakeVecIndex(), save_every_n_batches=0)


def test_vec_index_batch_writer_short_circuits_when_vec_index_is_none() -> None:
    """When ``vec_index=None`` (worker_writes_vec_index off, #335),
    the writer is still a usable context manager — every method is a
    no-op so the embed loop runs end-to-end without touching usearch.

    Pins the contract that the once-per-run lifecycle works
    identically whether the operator has the writer enabled or not.
    Without this the lifecycle test would have to branch on the
    writer-disabled feature flag and the regression #375 guards
    against would slip through in that branch.

    Sabotage-proven: drop the ``if self._vec_index is None: return``
    short-circuit from :meth:`VecIndexBatchWriter.add_batch` and
    rerun. The bare ``self._vec_index.add_vectors(...)`` then raises
    ``AttributeError`` on None. Restored after.
    """
    writer = VecIndexBatchWriter(None, save_every_n_batches=2)
    with writer:
        writer.add_batch(
            matched=[{"hash": "h0", "seq": 0}],
            vectors=[[0.0] * 4],
            batch_idx=0,
        )
        writer.add_batch(
            matched=[{"hash": "h1", "seq": 0}],
            vectors=[[0.0] * 4],
            batch_idx=1,
        )
    # No assertion on call counts — the contract is "doesn't raise";
    # the absence of an AttributeError IS the assertion.
    assert writer.batches_added == 0, (
        "vec_index=None → add_batch is a no-op; batches_added must stay at 0 "
        "so the final-save guard (which checks batches_added) doesn't fire"
    )
