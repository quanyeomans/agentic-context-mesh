"""Soak: vector-store-vs-content-vectors drift stays bounded across 10k chunks.

ADR-024 Bundle F seed soak test. Pins the
``vector-store-vs-content-vectors`` integrity invariant (defined in
``kairix/core/db/integrity.py``) at production scale: after embedding
N chunks, the gap between ``len(usearch_index)`` and
``COUNT(*) FROM content_vectors`` stays within the production
tolerance (``_VECTOR_STORE_TOLERANCE = 5%``).

The integrity check is the operator-facing canary that surfaces "your
ANN index lags content_vectors" — at fixture scale (N=100) any drift
falls below the per-cent rounding so the check trivially passes.
This soak test forces 10k chunks through the embed pipeline so the
tolerance actually constrains behaviour.

Composition: uses :func:`kairix.core.embed.embed.run_embed` (the
production entry point — no ``build_embed_pipeline`` factory ships
today; F47 scopes to ``tests/integration/`` so ``tests/soak/`` is
exempt) with :class:`EmbedDependencies` carrying scripted fakes for
every external call (Azure config, preflight, embed_batch). The
vec_index is a Protocol-compliant in-process stand-in so the test
runs without a real usearch file.

Wall-clock budget: < 10 min (asserted). Embedding 10k chunks against
the deterministic fake is ~30-90s on the soak runner; the 10 min
ceiling absorbs slow-runner variance and the integrity preflight
re-run cost.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.integrity import (
    INVARIANT_VECTOR_STORE,
    check_integrity,
)
from kairix.core.db.schema import create_schema
from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed
from tests.fakes import seed_bulk_content_rows

pytestmark = pytest.mark.soak

# Production-scale fixture: 10k content rows. Each row chunks to one
# embed-pipeline batch entry (the body is short enough to stay under
# CHUNK_SIZE_CHARS). 10k meets the ADR-024 soak floor.
_SOAK_N_ROWS = 10_000

# Wall-clock budget — the deterministic fake embed_batch returns
# vectors in O(N); the slow path is the per-batch SQLite write +
# vec_index.add_vectors call. Local measurement: ~30s. 10 min absorbs
# slow-runner variance.
_WALL_CLOCK_BUDGET_SECONDS = 600

# Embedding dimensions — must match DEFAULT_DIMS so preflight passes.
# We override at the dep boundary so this value is the single source
# of truth for the test fixture.
_EMBED_DIMS = 1536

# Per-batch size for run_embed. Production uses 250; we override to a
# larger value here so the test runs in fewer batches (less per-batch
# fixed cost — preflight, get_pending_chunks, etc).
_EMBED_BATCH_SIZE = 500

# Drift bound — the production integrity check tolerates 5% drift
# (_VECTOR_STORE_TOLERANCE in integrity.py). At 10k content_vectors
# the absolute tolerance is 500 rows. Soak tests assert STRICTER (zero
# drift after a clean embed cycle) because a deterministic fake should
# index every embedded vector. Catches regressions where the
# vec_index add path silently drops vectors at high N.
_MAX_DRIFT_ROWS = 0


class _SoakVecIndex:
    """In-process Protocol-compliant vec_index for the soak test.

    Implements the three methods :func:`run_embed` touches:
    ``add_vectors(keys, vectors)``, ``save()``, and ``__len__``. Tracks
    every (key, vector) added so the post-embed integrity check can
    compare ``len(index)`` to ``COUNT(*) FROM content_vectors``.

    The production usearch path uses a memory-mapped file; the
    behavioural contract the integrity check depends on is
    ``len(index) == number-of-vectors-added``. This fake satisfies that
    contract exactly so the drift assertion proves the production
    invariant holds at scale.
    """

    def __init__(self) -> None:
        # Track per (key) so duplicate-key adds don't double-count —
        # mirrors the usearch upsert-on-key semantics.
        self._keys: set[str] = set()
        self._add_calls: int = 0
        self._save_calls: int = 0

    def add_vectors(self, keys: list[str], vectors: list[list[float]]) -> None:
        del vectors  # only the keys matter for the drift assertion
        self._add_calls += 1
        for k in keys:
            self._keys.add(k)

    def save(self) -> None:
        self._save_calls += 1

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def add_calls(self) -> int:
        return self._add_calls

    @property
    def save_calls(self) -> int:
        return self._save_calls


def _fake_embed_batch(texts: list[str], *_args: Any, **_kwargs: Any) -> list[list[float]]:
    """Deterministic embed_batch fake — one vector per text, fixed dims.

    Returns a vector whose first element is the text length (just so
    different texts produce different vectors — soak tests don't care
    about the embedding quality, only that the count flows through
    correctly).
    """
    return [[float(len(t))] + [0.1] * (_EMBED_DIMS - 1) for t in texts]


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection with the production schema applied."""
    db = sqlite3.connect(str(tmp_path / "vector_drift_soak.sqlite"))
    create_schema(db)
    return db


def test_vector_index_drift_at_scale_stays_within_tolerance(tmp_path: Path) -> None:
    """10k chunks embedded → ``|index| == |content_vectors|``; integrity check passes.

    Concrete observable outcomes asserted:

      1. Pre-embed: content_vectors empty, vec_index empty.
      2. run_embed reports embedded == 10k, failed == 0.
      3. Post-embed: content_vectors row count == 10_000.
      4. Post-embed: vec_index length == 10_000.
      5. Drift == 0 (vec_index is in lock-step with content_vectors).
      6. Production integrity check ``_check_vector_store_vs_content_vectors``
         returns None (no gap surfaced).
      7. Wall-clock < 10 min.

    Sabotage proof: edit ``_SoakVecIndex.add_vectors`` to drop every
    other key (``for k in keys[::2]: self._keys.add(k)``); embed runs
    fine, ``len(index)`` reports 5k, content_vectors reports 10k,
    assertion 5 (drift==0) fails with concrete mismatch
    ``cv=10000 idx=5000 drift=5000``. Verified locally before commit.
    """
    db = _open_db(tmp_path)
    try:
        # 1. Pre-embed invariant — empty everywhere.
        n_seeded = seed_bulk_content_rows(db, n_rows=_SOAK_N_ROWS, collection="soak")
        assert n_seeded == _SOAK_N_ROWS, f"bulk seed should insert {_SOAK_N_ROWS}; got {n_seeded}"
        cv_pre = int(db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0])
        assert cv_pre == 0, f"pre-embed content_vectors should be empty; got {cv_pre}"

        vec_index = _SoakVecIndex()
        assert len(vec_index) == 0, "pre-embed vec_index should be empty"

        # 2. Compose EmbedDependencies — production shape with every
        # external call replaced by a deterministic in-process callable.
        # No env vars, no real Azure client, no monkeypatching.
        deps = EmbedDependencies(
            get_azure_config=lambda: ("soak-key", "https://soak.example.invalid", "soak-deploy"),
            preflight_check=lambda *_a, **_kw: _EMBED_DIMS,
            embed_batch=_fake_embed_batch,
            open_usearch_index=lambda: vec_index,
            migrate_content_vectors=lambda _db: None,
            get_document_root=lambda: None,
        )

        # 3. Drive the production embed entry point. Wall-clock measured
        # around the full cycle (gather + per-batch embed + per-batch
        # write + final save).
        started_at = time.monotonic()
        result = run_embed(db, batch_size=_EMBED_BATCH_SIZE, deps=deps)
        elapsed_s = time.monotonic() - started_at

        # 4. Embed reported full success.
        assert result["embedded"] == _SOAK_N_ROWS, (
            f"expected embedded={_SOAK_N_ROWS}; got {result['embedded']} (result={result})"
        )
        assert result["failed"] == 0, f"happy path: expected failed=0; got {result['failed']}"

        # 5. content_vectors row count matches the embedded count.
        cv_count = int(db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0])
        assert cv_count == _SOAK_N_ROWS, (
            f"content_vectors row count should equal embedded count; cv={cv_count} expected={_SOAK_N_ROWS}"
        )

        # 6. vec_index length matches the embedded count.
        idx_count = len(vec_index)
        assert idx_count == _SOAK_N_ROWS, (
            f"vec_index length should equal embedded count; idx={idx_count} expected={_SOAK_N_ROWS}"
        )

        # 7. Drift assertion — the soak invariant the production
        # integrity check approximates with a 5% tolerance.
        drift = abs(cv_count - idx_count)
        assert drift <= _MAX_DRIFT_ROWS, (
            f"vector-store-vs-content-vectors drift {drift} exceeds soak ceiling {_MAX_DRIFT_ROWS}: "
            f"cv={cv_count} idx={idx_count}. fix: investigate the embed-pipeline "
            f"vec_index.add_vectors call site; the production integrity check "
            f"({INVARIANT_VECTOR_STORE}) tolerates 5% but the soak test asserts strict parity."
        )

        # 8. Production integrity check confirms the invariant — runs
        # the public ``check_integrity`` entry against the same db + a
        # loader returning our vec_index. The vector-store invariant
        # MUST NOT surface as a gap; other invariants (e.g.
        # documents_without_fts) are outside this test's scope but the
        # vector-store gap list is asserted explicitly empty.
        report = check_integrity(db, vector_store_loader=lambda: vec_index)
        vector_gaps = [g for g in report.gaps if g.invariant == INVARIANT_VECTOR_STORE]
        assert vector_gaps == [], f"integrity check surfaced vector-store gap when drift should be zero: {vector_gaps}"

        # 9. Wall-clock budget.
        assert elapsed_s < _WALL_CLOCK_BUDGET_SECONDS, (
            f"embed wall-clock {elapsed_s:.1f}s exceeded budget of {_WALL_CLOCK_BUDGET_SECONDS}s "
            f"for {_SOAK_N_ROWS} chunks. fix: profile _embed_and_store_batch or raise the budget with rationale."
        )

        # 10. Sanity: save was called at least once (the embed cycle
        # ends with _save_index_checkpoint). Without this assertion a
        # regression that removed the final save would still pass the
        # drift check (because the test holds the fake in memory) but
        # silently break the on-disk usearch persistence in production.
        assert vec_index.save_calls >= 1, (
            f"embed should call vec_index.save() at least once; got {vec_index.save_calls}"
        )
    finally:
        db.close()
