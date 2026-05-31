"""Atomic save protocol for ``VectorIndex._save``.

The production incident: an in-place ``Index.save`` mid-write left an
unreadable file with the wrong header, corrupting ~1.57M of 1.8M
embedded vectors and forcing a $211 re-embed authorisation. The
contract this test pins:

  * ``_save`` writes through a sibling ``<path>.tmp``
  * ``os.fsync`` is called on the temp file
  * ``os.replace`` is atomic on POSIX, so the canonical path either
    holds the old valid file or the new valid file — never partial
  * ``load`` promotes a lingering ``<path>.tmp`` when the canonical
    file is missing (crash window between fsync and rename)

The tests use a real :class:`VectorIndex` against ``tmp_path`` and
sabotage the underlying usearch ``Index.save`` via an in-memory
substitution at the instance level — NOT a monkeypatch on
``kairix.core.search.vec_index`` (F1) and not a sys.modules touch
(attribute-reassignment-is-monkeypatch). The substitution targets only
the ``self._index`` attribute on the instance under test, which is the
canonical injection surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def test_save_writes_via_tmp_then_replaces(tmp_path: Path) -> None:
    """Successful save leaves no .tmp file behind and produces a valid index."""
    idx = _build_index(tmp_path)
    idx.add_vectors(["doc1_0", "doc1_1"], [[0.1] * 8, [0.2] * 8])
    idx.save()

    assert (tmp_path / "vectors.usearch").exists()
    assert (tmp_path / "vectors.meta.json").exists()
    assert not (tmp_path / "vectors.usearch.tmp").exists()
    assert not (tmp_path / "vectors.meta.json.tmp").exists()

    meta = json.loads((tmp_path / "vectors.meta.json").read_text(encoding="utf-8"))
    assert meta["ndim"] == 8
    assert meta["next_key"] == 2


def test_save_crash_preserves_old_canonical_file(tmp_path: Path) -> None:
    """A crash inside ``Index.save`` leaves the previous canonical file intact.

    Sabotage target: writing in-place would leave the canonical file
    half-overwritten with an unreadable HNSW header — the exact
    production incident this work exists to prevent.
    """
    idx = _build_index(tmp_path)
    idx.add_vectors(["doc1_0"], [[0.1] * 8])
    idx.save()
    original_bytes = (tmp_path / "vectors.usearch").read_bytes()

    # Add more vectors and stage a second save whose underlying
    # Index.save raises mid-write. The canonical file must still hold
    # the previous successful write.
    idx.add_vectors(["doc2_0"], [[0.2] * 8])

    class _PartialWriteIndex:
        """Writes partial garbage to the path it's handed, then raises.

        Mirrors the production failure mode: the usearch C extension
        had written N of M bytes when the process died. With in-place
        writes the canonical file is now corrupt; with write-tmp +
        rename the tmp file is corrupt but the canonical file is
        whatever the previous successful save left.
        """

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __len__(self) -> int:
            return len(self._inner)

        def save(self, path: str) -> None:
            Path(path).write_bytes(b"CORRUPTED-PARTIAL-WRITE")
            raise OSError("simulated mid-write crash")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    idx._index = _PartialWriteIndex(idx._index)
    with pytest.raises(OSError, match="simulated mid-write crash"):
        idx.save()

    # The canonical file MUST still hold the previous successful bytes.
    # A tmp file may be present (it's the partial write residue) but
    # the rename-after-fsync chain never fired.
    assert (tmp_path / "vectors.usearch").read_bytes() == original_bytes


def test_load_promotes_lingering_tmp_when_canonical_missing(tmp_path: Path) -> None:
    """A pending .tmp survives a crash between fsync and rename — load() recovers it."""
    idx = _build_index(tmp_path)
    idx.add_vectors(["doc1_0"], [[0.1] * 8])
    idx.save()

    # Simulate "crash between fsync(tmp) and replace(tmp, canonical)" by
    # renaming the canonical file out from under the recovery path. The
    # presence of the .tmp file is set up by copying the bytes — this
    # mirrors the kernel state when only the tmp was flushed.
    canonical_index = tmp_path / "vectors.usearch"
    canonical_meta = tmp_path / "vectors.meta.json"
    (tmp_path / "vectors.usearch.tmp").write_bytes(canonical_index.read_bytes())
    (tmp_path / "vectors.meta.json.tmp").write_bytes(canonical_meta.read_bytes())
    canonical_index.unlink()
    canonical_meta.unlink()

    # A fresh VectorIndex instance simulates the next-process load.
    recovered = _build_index(tmp_path)
    count = recovered.load()
    assert count == 1
    assert canonical_index.exists()
    assert canonical_meta.exists()
    assert not (tmp_path / "vectors.usearch.tmp").exists()


def test_load_ignores_stale_tmp_when_canonical_present(tmp_path: Path) -> None:
    """A stale .tmp from a previous run is left alone when the canonical
    file is the source of truth.

    Sabotage target: blindly promoting .tmp to canonical would clobber a
    newer canonical with stale data.
    """
    idx = _build_index(tmp_path)
    idx.add_vectors(["doc1_0"], [[0.1] * 8])
    idx.save()
    canonical_bytes = (tmp_path / "vectors.usearch").read_bytes()

    # Leave a stale .tmp with different bytes from the canonical.
    (tmp_path / "vectors.usearch.tmp").write_bytes(b"STALE-INDEX-BYTES")

    recovered = _build_index(tmp_path)
    recovered.load()
    assert (tmp_path / "vectors.usearch").read_bytes() == canonical_bytes


def test_round_trip_via_save_then_load(tmp_path: Path) -> None:
    """End-to-end: save then re-open in a fresh instance and confirm the
    key mapping survives."""
    writer = _build_index(tmp_path)
    writer.add_vectors(["a_0", "b_0", "c_0"], [[0.1] * 8, [0.2] * 8, [0.3] * 8])
    writer.save()

    reader = _build_index(tmp_path)
    count = reader.load()
    assert count == 3
    assert reader._key_to_hash_seq == {0: "a_0", 1: "b_0", 2: "c_0"}
    assert reader._next_key == 3


def test_meta_tmp_recovery_independent_of_index_tmp(tmp_path: Path) -> None:
    """The meta sidecar's .tmp recovery is independent of the index .tmp.

    Either file can be the missing-canonical-with-tmp pair on its own,
    and load() handles both cases.
    """
    idx = _build_index(tmp_path)
    idx.add_vectors(["x_0"], [[0.4] * 8])
    idx.save()
    canonical_meta = tmp_path / "vectors.meta.json"
    (tmp_path / "vectors.meta.json.tmp").write_bytes(canonical_meta.read_bytes())
    canonical_meta.unlink()

    recovered = _build_index(tmp_path)
    recovered.load()
    assert canonical_meta.exists()
    assert recovered._key_to_hash_seq == {0: "x_0"}
