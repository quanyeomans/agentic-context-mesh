"""#413 — vec_index next_key recovery on stale meta.

When the on-disk meta's ``next_key`` lags the actual max key in the
loaded usearch index (e.g. a save that wrote the index file but not
the meta, or a manual edit), ``add_vectors`` would previously
allocate keys via ``arange(_next_key, _next_key+N)`` that collided
with already-indexed keys — usearch raises
``RuntimeError: Duplicate keys not allowed in high-level wrappers``.

Pre-#375 the regression was masked because each batch closed +
reopened the handle, recomputing ``_next_key`` from the freshly-loaded
keys map every time. #375 keeps the handle open across batches, so a
single stale ``next_key`` at startup persists for the entire process.

The fix at :func:`VectorIndex.load` is defence-in-depth:
``_next_key = max(meta_next_key, inferred_next_key)`` where the
inferred fallback is ``max(self._key_to_hash_seq.keys()) + 1``. Trust
meta when consistent; recover when it lags.

F-rule discipline:
  - F1: no @patch; mutate the meta JSON directly on disk between
    constructor calls (filesystem injection, not code-path injection).
  - F8: ``pytestmark = pytest.mark.unit``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.core.search.vec_index import VectorIndex

pytestmark = pytest.mark.unit


def _build_index(tmp_path: Path, dim: int = 8) -> VectorIndex:
    return VectorIndex(
        index_path=tmp_path / "vectors.usearch",
        meta_path=tmp_path / "vectors.meta.json",
        db_path=tmp_path / "index.sqlite",
        ndim=dim,
        read_only=False,
    )


def test_stale_meta_next_key_does_not_collide_on_next_add(tmp_path: Path) -> None:
    """A reload that sees ``meta["next_key"]`` below the actual max key
    in the index recovers by computing ``max(meta, inferred)`` — the
    next ``add_vectors`` allocates fresh keys above the actual max
    instead of colliding.

    Sabotage-proof (executed locally):
      Reverted vec_index.py line 219 back to the original
      ``self._next_key = meta.get("next_key", ...)`` (trust-meta only).
      Confirmed this test failed at the ``add_vectors`` call with
      ``RuntimeError: Duplicate keys not allowed in high-level wrappers``.
      Restored.
    """
    # 1. Seed an index with 5 vectors → meta["next_key"]=5, keys=[0,1,2,3,4].
    idx1 = _build_index(tmp_path)
    idx1.add_vectors(
        ["d0", "d1", "d2", "d3", "d4"],
        [[float(i)] * 8 for i in range(5)],
    )
    idx1.save()

    # 2. Sabotage the meta file: drop next_key to a value LOWER than
    #    max(keys)+1, simulating a stale-meta save or operator edit.
    meta_path = tmp_path / "vectors.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actual_max = max(int(k) for k in meta["keys"].keys())
    assert actual_max == 4  # sanity — confirms the seed shape
    meta["next_key"] = 2  # ← stale: below actual max+1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # 3. Fresh instance reloads the index + (stale) meta.
    idx2 = _build_index(tmp_path)
    idx2.load()
    # The fix recomputes: _next_key = max(2, 5) = 5.
    # Without the fix, _next_key = 2 → next add allocates [2,3,...]
    # which collide with existing keys 2,3,4.
    idx2.add_vectors(
        ["d5", "d6", "d7"],
        [[float(i)] * 8 for i in range(5, 8)],
    )
    # If the fix is missing, the line above raises RuntimeError.
    # If the fix works, the keyspace is now [0..7].
    idx2.save()

    final_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    final_keys = sorted(int(k) for k in final_meta["keys"].keys())
    assert final_keys == list(range(8)), (
        f"vec_index next_key recovery regression — keyspace should be 0..7 "
        f"after recovery + 3 new adds, got {final_keys}. "
        f"final next_key={final_meta['next_key']}"
    )
    assert final_meta["next_key"] == 8


def test_consistent_meta_next_key_is_honoured(tmp_path: Path) -> None:
    """When meta's ``next_key`` matches the inferred value, the fix
    doesn't perturb anything — ``max(meta, inferred)`` collapses to
    the same number, no behaviour change vs the old code.

    Sabotage-proof: change the fix from ``max(meta, inferred)`` to
    ``min(meta, inferred)``; this test fails at the equality check
    because next_key drops below the consistent value.
    """
    idx1 = _build_index(tmp_path)
    idx1.add_vectors(["d0", "d1", "d2"], [[float(i)] * 8 for i in range(3)])
    idx1.save()

    idx2 = _build_index(tmp_path)
    idx2.load()
    # Meta says next_key=3, keys go up to 2 → inferred next_key=3 → max=3.
    idx2.add_vectors(["d3"], [[3.0] * 8])
    idx2.save()

    final_meta = json.loads((tmp_path / "vectors.meta.json").read_text(encoding="utf-8"))
    assert final_meta["next_key"] == 4
    assert sorted(int(k) for k in final_meta["keys"].keys()) == [0, 1, 2, 3]


def test_meta_next_key_ahead_of_max_key_is_preserved(tmp_path: Path) -> None:
    """When meta's ``next_key`` is AHEAD of max(keys)+1 (e.g. operator
    pre-allocated a gap, or an in-flight save bumped next_key before
    writing the keys row), the fix keeps meta's value — ``max(meta,
    inferred)`` picks the larger.

    Sabotage-proof: change the fix to use the inferred value
    unconditionally; this test fails because next_key drops to the
    lower inferred value, which a subsequent add would then collide
    with the operator-reserved gap.
    """
    idx1 = _build_index(tmp_path)
    idx1.add_vectors(["d0", "d1"], [[float(i)] * 8 for i in range(2)])
    idx1.save()

    meta_path = tmp_path / "vectors.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["next_key"] = 10  # ← ahead: reserves keys 2..9 as a gap
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    idx2 = _build_index(tmp_path)
    idx2.load()
    # max(10, 2) = 10 → next add allocates key 10, preserving the gap.
    idx2.add_vectors(["d10"], [[10.0] * 8])
    idx2.save()

    final_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert final_meta["next_key"] == 11
    # The gap stays intact — keys are 0, 1, 10.
    assert sorted(int(k) for k in final_meta["keys"].keys()) == [0, 1, 10]
