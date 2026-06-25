"""Soak: the re-chunk sweep stays bounded (F66) and converges the whole corpus.

Seeds 10k stale documents and runs the sweep tick repeatedly: every tick scans
at most ``cap`` documents (the per-tick budget), the persisted cursor walks the
table so the sweep makes forward progress, and after one full pass every
document's chunker_version has converged to the registry version. Validates the
production-safety claim that a single tick never scans the whole table.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.chunker_registry import build_default_registry
from kairix.core.connectors.rechunk_sweep import (
    expected_chunker_version,
    run_rechunk_sweep,
    scan_candidates,
)
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.soak

_N = 10_000
_CAP = 500
_TS = "2026-06-25T00:00:00Z"
_MD = "# Heading\n\nA short paragraph of body text for chunking."


def _seed_stale(db: sqlite3.Connection, n: int) -> None:
    media = []
    source = []
    docs = []
    for i in range(n):
        h = f"raw{i:06d}"
        uri = f"obsidian://note-{i:06d}"
        media.append((h, f"item-{i:06d}", "text/markdown", "ok", "stale-v0"))
        source.append((h, uri, _MD, _TS))
        docs.append((f"{uri}#0", f"chunk-{i:06d}", uri))
    db.executemany(
        "INSERT INTO documents_media (hash, path, format, extraction_status, chunker_version) VALUES (?, ?, ?, ?, ?)",
        media,
    )
    db.executemany(
        "INSERT INTO silver_source (hash, source_uri, markdown, created_at) VALUES (?, ?, ?, ?)",
        source,
    )
    db.executemany(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, source_modified_at, "
        "source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', ?, ?, 'obsidian', ?, '2026-06-25T00:00:00Z', NULL, 'internal', "
        "'2026-06-25T00:00:00Z', '2026-06-25T00:00:00Z', 1)",
        docs,
    )
    db.commit()


def test_rechunk_sweep_is_bounded_per_tick_and_converges_at_10k() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    registry = build_default_registry()
    _seed_stale(db, _N)

    expected = expected_chunker_version(registry, kind="obsidian", mime="text/markdown")
    assert expected != "stale-v0", "the seeded docs must start stale"

    ticks = 0
    total_rechunked = 0
    max_scanned = 0
    while True:
        result = run_rechunk_sweep(db, cap=_CAP, registry=registry)
        ticks += 1
        max_scanned = max(max_scanned, result.scanned)
        total_rechunked += result.rechunked
        assert result.scanned <= _CAP, f"tick {ticks} scanned {result.scanned} > cap {_CAP}"
        assert result.failed == 0
        if result.cursor_advanced_to == "":
            break
        assert ticks <= (_N // _CAP) + 2, "the cursor must reach the table end in bounded ticks"

    # F66: a single tick never scanned the whole table.
    assert max_scanned <= _CAP
    # Every document re-chunked exactly once over the pass.
    assert total_rechunked == _N
    # The whole corpus converged to the registry version.
    stale_remaining = db.execute(
        "SELECT COUNT(*) FROM documents_media WHERE chunker_version != ?", (expected,)
    ).fetchone()[0]
    assert stale_remaining == 0

    # A subsequent pass finds nothing stale — the sweep is idempotent at rest.
    settled, scanned, _ = scan_candidates(db, registry, cap=_CAP, cursor="")
    assert scanned <= _CAP
    assert settled == []
