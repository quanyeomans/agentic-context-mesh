"""Per-batch WAL checkpoint during embed catch-up (GH #394).

R3 (#389, shipped 2026-05-29) added a 10-minute maintenance tick that
runs ``PRAGMA wal_checkpoint(TRUNCATE)``. The tick can't fire while
embed is mid-transaction, so during a 678K backfill the WAL grew to
3.8 GB before R3 could reclaim. This test pins the per-batch
``PRAGMA wal_checkpoint(PASSIVE)`` call inside the embed loop so the
WAL stays bounded throughout catch-up runs.

PASSIVE (not TRUNCATE) — TRUNCATE blocks readers, PASSIVE returns the
checkpoint tuple without waiting. Production must not block readers
mid-embed.

Sabotage-proof: mutate ``(batch_idx + 1) % every_n == 0`` to ``== 1``
or drop the call to ``_maybe_wal_checkpoint`` from ``_run_embed_loop_serial``
and re-run — the test fails with the captured-count assertion. Restore,
re-run, pass.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.pipeline import EmbedPipeline

pytestmark = pytest.mark.unit


# F1: no @patch / monkeypatch on kairix internals — we wrap the
# sqlite3.Connection in a counting proxy that intercepts execute() calls
# and forwards everything else, so the real DB still owns the data path
# and the embed code reaches a real ``PRAGMA wal_checkpoint`` site.
class _CheckpointCountingConn:
    """Thin wrapper around a real sqlite3.Connection that counts
    ``PRAGMA wal_checkpoint`` calls.

    Every other attribute / method is forwarded verbatim so the embed
    code path is exercised end-to-end against a real SQLite — only the
    one PRAGMA call site is observable.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.checkpoint_calls: int = 0

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "wal_checkpoint" in sql.lower():
            self.checkpoint_calls += 1
        return self._real.execute(sql, *args, **kwargs)

    # Forward every other attribute access (commit/cursor/close/etc.)
    # straight through to the underlying connection.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    # Context-manager protocol — the embed code uses ``with db:`` blocks
    # for transactional staging.
    def __enter__(self) -> Any:
        return self._real.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._real.__exit__(*exc)


def _make_test_db(tmp_path: Any) -> sqlite3.Connection:
    """Create a WAL-mode SQLite DB with the schema the embed loop needs.

    File-backed (not ``:memory:``) so the WAL machinery is real — PRAGMA
    wal_checkpoint is only meaningful against a journal_mode=WAL DB on
    disk. Tests can still pass deterministic assertions because the
    wrapper counts the PRAGMA invocations, not the resulting page deltas.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """
        CREATE TABLE documents (
            hash TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT,
            collection TEXT,
            active INTEGER DEFAULT 1
        )
        """
    )
    db.execute(
        """
        CREATE TABLE content (
            hash TEXT PRIMARY KEY,
            doc TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE content_vectors (
            hash TEXT,
            seq INTEGER,
            pos INTEGER,
            model TEXT,
            embedded_at INTEGER,
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        )
        """
    )
    return db


def _make_fake_deps(embed_dim: int = 1536) -> EmbedDependencies:
    """All-fake EmbedDependencies — no Azure, no usearch, no real schema migration.

    Returns deterministic 0.01-valued vectors of ``embed_dim`` so the
    embed loop runs all the way through to the DB write step without
    needing network or filesystem services.
    """
    return EmbedDependencies(
        get_azure_config=lambda: ("fake-key", "https://fake.endpoint", "fake-model"),
        preflight_check=lambda _k, _e, _d: embed_dim,
        embed_batch=lambda texts, *a, **kw: [[0.01] * embed_dim for _ in texts],
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
    )


def _seed_documents(db: sqlite3.Connection, n: int) -> None:
    """Insert ``n`` short documents — each yields exactly one chunk."""
    for i in range(n):
        db.execute(
            "INSERT INTO documents (hash, path, title, collection) VALUES (?, ?, ?, ?)",
            (f"h{i}", f"/docs/doc{i}.md", f"Doc {i}", "default"),
        )
        db.execute(
            "INSERT INTO content (hash, doc) VALUES (?, ?)",
            (f"h{i}", f"Document {i} body."),
        )
    db.commit()


def test_wal_checkpoint_fires_every_n_batches(tmp_path: Any) -> None:
    """5 batches at cadence 2 should fire the PRAGMA exactly twice.

    Per ``_maybe_wal_checkpoint`` semantics: trigger when
    ``(batch_idx + 1) % every_n == 0``. With 5 chunks at batch_size=1
    and every=2, fires after batch_idx=1 (batch 2) and batch_idx=3
    (batch 4). Batches 1, 3, 5 do NOT trigger. Total = 2.
    """
    real_db = _make_test_db(tmp_path)
    _seed_documents(real_db, 5)

    counting_db = _CheckpointCountingConn(real_db)

    # Direct EmbedPipeline construction — this is a unit test (under
    # tests/core/embed/), not an integration test under tests/integration/,
    # so F47 (which only scans tests/integration/) does not apply. The
    # existing tests/embed/test_embed_pipeline.py uses the same pattern.
    pipeline = EmbedPipeline(db=counting_db, deps=_make_fake_deps())

    # batch_size=1 makes each of the 5 chunks its own batch — keeps the
    # batch count exact and the cadence assertion crisp.
    result = pipeline.run(
        batch_size=1,
        wal_checkpoint_every_n_batches=2,
    )

    assert result["embedded"] == 5, f"expected 5 chunks embedded, got {result['embedded']}"
    assert counting_db.checkpoint_calls == 2, (
        f"expected 2 wal_checkpoint PRAGMAs (batches=5, every=2 → batch 2 + batch 4), "
        f"got {counting_db.checkpoint_calls}"
    )


def test_wal_checkpoint_disabled_when_every_n_is_zero(tmp_path: Any) -> None:
    """``wal_checkpoint_every_n_batches=0`` disables the per-batch hook.

    This is the safety lever for tests / operators who want to isolate
    the no-checkpoint baseline. The maintenance-loop tick (R3) still
    reclaims the WAL on its own cadence.
    """
    real_db = _make_test_db(tmp_path)
    _seed_documents(real_db, 5)

    counting_db = _CheckpointCountingConn(real_db)
    pipeline = EmbedPipeline(db=counting_db, deps=_make_fake_deps())

    result = pipeline.run(
        batch_size=1,
        wal_checkpoint_every_n_batches=0,
    )

    assert result["embedded"] == 5
    assert counting_db.checkpoint_calls == 0, (
        f"expected 0 wal_checkpoint PRAGMAs (cadence=0 → disabled), got {counting_db.checkpoint_calls}"
    )
