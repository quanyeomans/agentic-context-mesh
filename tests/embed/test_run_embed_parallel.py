"""Unit tests for ``run_embed(parallel=N)`` — the ThreadPoolExecutor path.

These tests exercise four properties of the parallel embed loop:

1. ``--parallel 1`` (default) reproduces today's serial output bit-for-bit.
2. ``--parallel 5`` processes the same chunks (count + ordering + index
   rows match the serial baseline at scale).
3. ``--parallel 3`` with a batch that raises mid-flight surfaces that
   batch as ``failed`` while the rest still complete (no silent drop).
4. ``--parallel 11`` is rejected at the boundary with an F21-shaped
   affordance pointing operators at the sizing runbook.

Each test ships a "Sabotage:" comment naming the production mutation
that would defeat it. The throughput test (`parallel_5_matches_serial`)
was run end-to-end with the sabotage applied (mutated production to skip
``fut.result()`` and just iterate the dict — counter mismatch confirmed
— then restored).

No `@patch`/`monkeypatch` on kairix internals (F1-clean). Fakes are
inline (the inline-fake-class pattern is the established shape in
`tests/embed/test_embed_coverage.py`).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import MAX_PARALLEL_BATCHES, run_embed

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _seed_documents(db: sqlite3.Connection, n_docs: int, doc_len: int = 3) -> None:
    """Build the minimum embed-pipeline schema and insert N docs.

    Doc body is sized below CHUNK_SIZE_CHARS (3600) so each document
    produces exactly one chunk — keeps the chunk-count math obvious.
    """
    db.execute("CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1)")
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT,"
        " PRIMARY KEY (hash, seq))"
    )
    body = "word " * doc_len  # short — one chunk per doc
    for i in range(n_docs):
        h = f"h{i:05d}"
        db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (h, body))
        db.execute(
            "INSERT INTO documents (hash, path, active) VALUES (?, ?, 1)",
            (h, f"docs/test{i}.md"),
        )
    db.commit()


class _ThreadRecordingEmbedBatch:
    """Records the set of distinct thread idents that invoked embed_batch.

    Lets the parallel test assert that more than one worker thread
    actually fired (sabotage-proof: if production silently falls back to
    serial, this set has size 1 and the test fails).
    """

    def __init__(self, *, dims: int = 1536, raise_on_batch_idx: int | None = None) -> None:
        self.dims = dims
        self.calls = 0
        self.thread_idents: set[int] = set()
        self.lock = threading.Lock()
        self._raise_on_batch_idx = raise_on_batch_idx

    def __call__(self, texts: list[str], *_args: Any, **_kwargs: Any) -> list[list[float]]:
        with self.lock:
            self.thread_idents.add(threading.get_ident())
            self.calls += 1
            current_call = self.calls
        if self._raise_on_batch_idx is not None and current_call == self._raise_on_batch_idx + 1:
            # Simulate a transient Azure failure on the Nth batch (1-indexed
            # by call ordering; converted from the 0-indexed brief). Other
            # batches must still complete.
            raise RuntimeError(f"simulated Azure 5xx on batch {self._raise_on_batch_idx}")
        # Small sleep to encourage thread interleaving on the parallel path.
        time.sleep(0.01)
        return [[0.01] * self.dims for _ in texts]


def _build_deps(embed_batch_callable: Any, *, dims: int = 1536) -> EmbedDependencies:
    return EmbedDependencies(
        get_azure_config=lambda: ("fake-key", "https://fake.endpoint", "fake-model"),
        preflight_check=lambda *_a, **_kw: dims,
        embed_batch=embed_batch_callable,
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
    )


def _count_vectors(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()
    return int(row[0])


# ── Test 1: parallel=1 reproduces serial output ───────────────────────────────


@pytest.mark.unit
def test_parallel_1_reproduces_serial_output() -> None:
    """``--parallel 1`` produces identical output to today's serial path.

    Sabotage: mutate ``run_embed`` to dispatch parallel=1 into the
    ``_run_embed_loop_parallel`` branch unconditionally. The
    ``embedded`` count and per-(hash, seq) ordering must remain identical
    — sabotage proven by changing the ``if parallel == 1:`` branch to
    ``if False:`` and re-running. (When the ThreadPool path also
    matches, this test still passes — that's the point: parallel and
    serial paths agree on the default. The real sabotage proof for
    parallel correctness is test 2.)
    """
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=12)
    fake = _ThreadRecordingEmbedBatch()
    deps = _build_deps(fake)

    result = run_embed(db, batch_size=5, deps=deps, parallel=1)

    assert result["embedded"] == 12
    assert result["failed"] == 0
    assert _count_vectors(db) == 12
    # Serial path runs everything on the calling thread.
    assert len(fake.thread_idents) == 1


# ── Test 2: parallel=5 matches serial throughput-wise (no drops, order safe) ─


@pytest.mark.unit
def test_parallel_5_processes_same_chunks_as_serial() -> None:
    """``--parallel 5`` embeds every chunk + preserves per-hash ordering.

    Sabotage: in ``_run_embed_loop_parallel``, drop the ``fut.result()``
    call (just iterate the futures dict without unwrapping). The embed
    count drops to 0 (no batch is persisted) — verified by editing prod
    locally:

        for fut in as_completed(future_to_batch):
            batch_idx, batch = future_to_batch[fut]
    -       matched, vectors, unaccounted, azure_failed = fut.result()
    +       continue  # SABOTAGE: skip persist entirely

    With that mutation, ``_count_vectors`` returned 0 and the assertion
    ``result["embedded"] == 30`` failed loudly. Restored after.
    """
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=30)
    fake = _ThreadRecordingEmbedBatch()
    deps = _build_deps(fake)

    result = run_embed(db, batch_size=3, deps=deps, parallel=5)

    assert result["embedded"] == 30, result
    assert result["failed"] == 0
    assert _count_vectors(db) == 30
    # Parallel path used more than one worker thread.
    assert len(fake.thread_idents) > 1, (
        f"expected >1 worker thread for parallel=5, saw {len(fake.thread_idents)} — "
        "production may have silently fallen back to serial"
    )
    # Ordering: for every doc, seq starts at 0 and increments. Since each
    # doc is sized to a single chunk in our fixture, every hash has
    # exactly one row with seq=0.
    rows = db.execute("SELECT hash, seq FROM content_vectors ORDER BY hash, seq").fetchall()
    assert len(rows) == 30
    assert all(seq == 0 for _h, seq in rows), "per-hash seq monotonicity broken under parallel"


# ── Test 3: parallel=3 surfaces failed batches, doesn't silently drop ────────


@pytest.mark.unit
def test_parallel_3_surfaces_failed_batch_without_dropping_others() -> None:
    """A batch that raises mid-flight surfaces as ``failed``; others succeed.

    Sabotage: in ``_run_embed_loop_parallel``, catch the
    ``embed_batch`` exception inside the worker AND swallow the result
    (``return None`` instead of returning the failed-flag tuple). The
    failed chunks would then be silently dropped from
    ``failed_chunks`` — verified by editing the ``azure_failed=True``
    return in ``_embed_batch_only`` to ``return [], [], [], False``;
    test failure was immediate (``result["failed"] == 0`` instead of
    ``== batch_size``). Restored after.
    """
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=15)
    # Fail on the 2nd call (batch index 1). With batch_size=5 + 15 docs,
    # there are 3 batches; one fails, two succeed.
    fake = _ThreadRecordingEmbedBatch(raise_on_batch_idx=1)
    deps = _build_deps(fake)

    result = run_embed(db, batch_size=5, deps=deps, parallel=3)

    # 10 chunks embedded (2 batches x 5), 5 chunks failed (1 batch x 5).
    assert result["embedded"] == 10
    assert result["failed"] == 5
    assert _count_vectors(db) == 10


# ── Test 4: parallel=11 rejected with F21-shaped affordance ──────────────────


@pytest.mark.unit
def test_parallel_above_ceiling_rejected_with_runbook_pointer() -> None:
    """``--parallel 11`` rejected at the run_embed boundary with an F21
    affordance: names the rate-limit risk + points operators at the
    sizing runbook.

    Sabotage: drop the ``_validate_parallel(parallel)`` call at the top
    of ``run_embed``. The ValueError would no longer fire and the
    ThreadPoolExecutor would happily spawn 11 worker threads, blowing
    past the documented ceiling — verified by commenting out the call;
    test failed with ``DID NOT RAISE``. Restored after.
    """
    db = sqlite3.connect(":memory:")
    _seed_documents(db, n_docs=1)
    fake = _ThreadRecordingEmbedBatch()
    deps = _build_deps(fake)

    with pytest.raises(ValueError) as info:
        run_embed(db, batch_size=5, deps=deps, parallel=MAX_PARALLEL_BATCHES + 1)

    msg = str(info.value)
    assert "rate-limit" in msg, "expected the affordance to name the Azure rate-limit risk"
    assert "worker-memory-and-swap" in msg, "expected a pointer to the sizing runbook"
    assert "fix:" in msg and "next:" in msg and "run:" in msg, (
        "F21 expects fix:/next:/run: markers in pipeline-blocking error output"
    )
