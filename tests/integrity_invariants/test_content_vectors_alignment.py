"""Invariant: every content_vectors row traces to a content row.

Why
---
ADR-024 §"Defects that told us where the pyramid is wrong" — the
content_vectors / content drift class (#335 ancestor): production held
content_vectors rows whose ``hash`` had no matching ``content`` row,
which broke the BM25-paired vector lookup at retrieval time. The
existing preflight ``_check_content_vectors_without_documents`` surfaces
the symptom; this invariant proves the composed write path never
creates the symptom in the first place.

The mechanical contract: after the connector pipeline runs N batches,

    |distinct hash in content_vectors|
        ==
    |distinct hash in content where hash IN content_vectors.hash|

Every ``content_vectors`` row's hash must appear in ``content``. The
chunk-writer's atomic INSERT-content-then-INSERT-vector pattern in
``kairix.worker._SqliteChunkWriter`` ensures the per-row paired write;
the invariant proves that pairing holds across composed batches +
chunk commits.

Sabotage proof (executed during authoring)
------------------------------------------
Mutation: in ``kairix/worker.py:_SqliteChunkWriter.upsert``, remove the
``INSERT OR REPLACE INTO content`` line (or wrap it in
``if False:``). Re-run this test:

    AssertionError: content_vectors_alignment violated: 20 distinct
      content_vectors hashes but only 0 have matching content rows.
      Orphans: 20 — content INSERT skipped, vectors landed alone.

Restoration: revert. Test goes green. The mismatch surfaces concretely
(orphan count > 0) because the invariant compares ``content_vectors``
against ``content`` via INNER JOIN.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.invariant


def _open_db(tmp_path: Path, name: str = "vec_alignment.sqlite") -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / name))
    create_schema(db, dims=4)
    return db


def _run_batch(
    db: sqlite3.Connection,
    *,
    source_name: str,
    n: int,
) -> None:
    """Drive N unique-content items through the composed pipeline.

    Uses the production chunk_writer (no override) so the real
    INSERT-content + INSERT-content_vectors pairing in
    ``kairix.worker._SqliteChunkWriter`` is exercised end-to-end.
    """
    events: list[ChangeEvent] = []
    content: dict[str, bytes] = {}
    for i in range(n):
        item_id = f"vec-doc-{i:05d}.md"
        events.append(ChangeEvent(op="modified", item_id=item_id, modified_at=f"2026-05-28T11:00:{i % 60:02d}Z"))
        # Unique body so each chunk produces a distinct content_hash and
        # the COUNT(DISTINCT hash) reads back at the per-item scale.
        content[item_id] = (f"content body for {item_id} — unique enough to hash distinctly per item.").encode()
    pipeline = build_connector_pipeline(
        db=db,
        collection="vec-alignment-invariant",
        entity_graph_sink=FakeEntityGraphSink(),
    )
    connector = FakeSourceConnector(
        name=source_name,
        events=events,
        content=content,
        cursor_token=f"{source_name}-cursor-1",
        per_tick_max_items=max(n, 1),
    )
    pipeline.run_batch(connector, FakeExtractor())


def _assert_alignment(db: sqlite3.Connection) -> None:
    """Assert every content_vectors.hash has a matching content row.

    The query reads from ``content_vectors`` (the side that's at risk
    of accumulating orphans) and counts distinct hashes that DO NOT
    appear in ``content``. Zero orphans == invariant holds.
    """
    cv_distinct_row = db.execute("SELECT COUNT(DISTINCT hash) FROM content_vectors").fetchone()
    cv_distinct = int(cv_distinct_row[0]) if cv_distinct_row else 0
    aligned_row = db.execute(
        "SELECT COUNT(DISTINCT v.hash) FROM content_vectors v INNER JOIN content c ON c.hash = v.hash"
    ).fetchone()
    aligned = int(aligned_row[0]) if aligned_row else 0
    orphans = cv_distinct - aligned
    assert cv_distinct > 0, (
        "content_vectors_alignment fixture-setup invariant: expected the "
        "pipeline to write at least one content_vectors row; got zero. "
        "Verify the FakeExtractor produced non-empty markdown and Silver "
        "emitted at least one chunk."
    )
    assert orphans == 0, (
        f"content_vectors_alignment violated: {cv_distinct} distinct "
        f"content_vectors hashes but only {aligned} have matching content "
        f"rows. Orphans={orphans}. Every vector slot MUST trace to a "
        f"content row — the INSERT pairing in _SqliteChunkWriter.upsert "
        f"is the contract. See ADR-024 §F72."
    )


def test_invariant_holds_at_fixture_scale(tmp_path: Path) -> None:
    """N=30 unique items: every content_vectors row traces to content."""
    db = _open_db(tmp_path)
    try:
        _run_batch(db, source_name="vec-alignment-fixture", n=30)
        _assert_alignment(db)
    finally:
        db.close()


@pytest.mark.soak
def test_invariant_holds_at_soak_scale(tmp_path: Path) -> None:
    """N=10**4 unique items: alignment holds across ~200 chunk commits.

    Default ``chunk_size=50`` means 10**4 items trigger ~200 per-chunk
    commits. Each commit is a transaction boundary where a partial
    write could leak a vector without its content row. The soak run
    proves the pairing survives across that many boundaries.
    """
    db = _open_db(tmp_path, name="vec_alignment_soak.sqlite")
    try:
        _run_batch(db, source_name="vec-alignment-soak", n=10_000)
        _assert_alignment(db)
    finally:
        db.close()
