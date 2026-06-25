"""Invariant: every active document ends up with a REAL embedded vector.

Why
---
The chunk-0 incident (#627). The connector pipeline's chunk writer lands a
model-NULL ``content_vectors(hash, seq, pos)`` placeholder per chunk; the embed
worker later promotes each placeholder to a real vector by setting ``model`` —
but only for chunks its DISCOVERY query surfaces. The shipped discovery query
joined ``AND v.seq = 0`` and selected ``WHERE v.hash IS NULL``, so a chunk whose
placeholder already sat at ``seq = 0`` was matched by the join and excluded from
embedding — it stayed a placeholder forever, never vector-searchable, while the
presence-only ``documents-without-vectors`` preflight (satisfied by the
placeholder alone) stayed green. The fix re-keyed discovery on
``AND v.model IS NOT NULL`` (a model-NULL placeholder no longer satisfies the
join, so every un-promoted chunk is surfaced).

This invariant proves the COMPOSED write+embed path never leaves an active
document placeholder-only: ingest N documents through the real
``build_connector_pipeline`` (the production chunk writer), run the real
``run_embed`` discovery+promotion, then assert zero documents match the state
predicate ``documents d LEFT JOIN content_vectors v ON v.hash = d.hash AND
v.model IS NOT NULL WHERE d.active = 1 AND v.hash IS NULL`` — both via raw SQL and
through the operator-facing ``check_integrity`` preflight.

Embedding is faked (deterministic fixed-dim vectors); the embedding CONTENT is
irrelevant — what's under test is that discovery surfaces every chunk for
promotion. ``_EMBED_DIMS`` is 1536 because ``run_embed``'s schema preflight
asserts the embedder's dimensions equal the schema's recorded dimensions; the
two must agree or ``run_embed`` raises ``SchemaVersionError`` before any work.

Sabotage proof (executed during authoring)
------------------------------------------
Mutation: revert the embed discovery join in
``kairix/core/embed/embed.py::_gather_pending_chunks`` from
``AND v.model IS NOT NULL`` back to ``AND v.seq = 0`` (the #627 bug). Re-run
this test:

    AssertionError: documents_embedded_completeness violated: 30 active
      documents but 30 have only an un-promoted placeholder ...

Empirically observed under the mutation: ``run_embed`` reports ``embedded=0`` and
all N documents read back as un-embedded. Restoration: revert — ``embedded=N`` and
zero un-embedded. The fail message names the count so the operator sees the scale.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.integrity import check_integrity
from kairix.core.db.schema import create_schema
from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.invariant

_INVARIANT = "documents-without-embedded-vector"

# run_embed's schema preflight requires the embedder's dimensionality to equal
# the dimensions recorded in the schema; a mismatch raises SchemaVersionError
# before any chunk is embedded. 1536 is the production default the schema records.
_EMBED_DIMS = 1536


def _open_db(tmp_path: Path, name: str = "embedded_completeness.sqlite") -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / name))
    create_schema(db, dims=_EMBED_DIMS)
    return db


def _fake_embed_deps() -> EmbedDependencies:
    """An ``EmbedDependencies`` that fakes every external call.

    ``open_usearch_index=lambda: None`` keeps the run off the real usearch path;
    the DB-side ``content_vectors.model`` promotion (what this invariant reads)
    happens regardless. ``embed_batch`` returns deterministic vectors — the
    invariant is invariant to vector content, only to WHETHER every chunk was
    surfaced for promotion.
    """
    return EmbedDependencies(
        get_azure_config=lambda: ("fake-key", "https://fake.endpoint", "fake-model"),
        preflight_check=lambda *_a, **_kw: _EMBED_DIMS,
        embed_batch=lambda texts, *_a, **_kw: [[0.01] * _EMBED_DIMS for _ in texts],
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
    )


def _ingest_batch(db: sqlite3.Connection, *, source_name: str, n: int) -> None:
    """Drive N unique-content documents through the composed pipeline.

    Uses the production chunk_writer (no override) so each document lands a real
    model-NULL ``content_vectors`` placeholder — exactly the pre-embed state the
    discovery query must surface.
    """
    events: list[ChangeEvent] = []
    content: dict[str, bytes] = {}
    for i in range(n):
        item_id = f"embed-doc-{i:05d}.md"
        events.append(ChangeEvent(op="modified", item_id=item_id, modified_at=f"2026-05-28T12:00:{i % 60:02d}Z"))
        # Unique body so each document hashes to a distinct content row.
        content[item_id] = (f"content body for {item_id} — unique enough to hash distinctly per item.").encode()
    pipeline = build_connector_pipeline(
        db=db,
        collection="embedded-completeness-invariant",
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


def _count_unembedded_active_documents(db: sqlite3.Connection) -> int:
    """Active documents whose only vector (if any) is an un-promoted placeholder."""
    row = db.execute(
        "SELECT COUNT(*) FROM documents d "
        "LEFT JOIN content_vectors v ON v.hash = d.hash AND v.model IS NOT NULL "
        "WHERE d.active = 1 AND v.hash IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0


def _assert_every_active_document_embedded(db: sqlite3.Connection) -> None:
    """After embed, no active document is placeholder-only — via SQL AND preflight."""
    active_row = db.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()
    active = int(active_row[0]) if active_row else 0
    promoted_row = db.execute("SELECT COUNT(*) FROM content_vectors WHERE model IS NOT NULL").fetchone()
    promoted = int(promoted_row[0]) if promoted_row else 0
    unembedded = _count_unembedded_active_documents(db)

    # Guard against a vacuous green: prove the pipeline+embed actually produced
    # real vectors rather than both sides happening to be zero.
    assert active > 0, (
        "documents_embedded_completeness fixture-setup invariant: expected the "
        "pipeline to write at least one active document; got zero. Verify the "
        "FakeExtractor produced non-empty markdown and Silver emitted a chunk."
    )
    assert promoted > 0, (
        "documents_embedded_completeness fixture-setup invariant: expected "
        "run_embed to promote at least one placeholder to a real (model-set) "
        "vector; got zero. Verify the fake embed_batch ran and the discovery "
        "query surfaced the pipeline's placeholders."
    )
    assert unembedded == 0, (
        f"documents_embedded_completeness violated: {active} active documents but "
        f"{unembedded} have only an un-promoted placeholder (model-NULL) — the "
        f"embed discovery query failed to surface them for promotion. This is the "
        f"chunk-0 #627 class. See ADR-024 §F72."
    )

    # The operator-facing preflight must agree there is no gap (F5: asserted
    # through the public check_integrity surface, picking the gap by its stable
    # public invariant id — never by the private _check_* name).
    report = check_integrity(db, vector_store_loader=lambda: None)
    gap = next((g for g in report.gaps if g.invariant == _INVARIANT), None)
    assert gap is None, (
        f"documents_embedded_completeness violated: check_integrity surfaced a "
        f"'{_INVARIANT}' gap of count={getattr(gap, 'count', '?')} after a full "
        f"embed cycle — the preflight an operator runs would report stuck "
        f"documents. sample={getattr(gap, 'sample', None)}"
    )


def test_invariant_holds_at_fixture_scale(tmp_path: Path) -> None:
    """N=30 documents: ingest → embed → every active document has a real vector.

    The pipeline runs against composed production code (factory chunk_writer)
    and the real ``run_embed`` discovery+promotion; the assertion proves the
    discovery query surfaced every chunk so no document is left placeholder-only.
    """
    db = _open_db(tmp_path)
    try:
        _ingest_batch(db, source_name="embedded-completeness-fixture", n=30)
        run_embed(db, batch_size=10, deps=_fake_embed_deps())
        _assert_every_active_document_embedded(db)
    finally:
        db.close()


@pytest.mark.soak
def test_invariant_holds_at_soak_scale(tmp_path: Path) -> None:
    """N=10**4 documents: completeness holds across batched discovery + promotion.

    Carries ``@pytest.mark.soak`` so CI Stage 3 skips it; the nightly soak runs
    it. At 10**4 documents the discovery query pages the pending set across many
    embed batches — a soak-scale regression where a batch-boundary or paging bug
    drops chunks (re-creating the chunk-0 silent-skip at scale) surfaces here.
    """
    db = _open_db(tmp_path, name="embedded_completeness_soak.sqlite")
    try:
        _ingest_batch(db, source_name="embedded-completeness-soak", n=10_000)
        run_embed(db, batch_size=500, deps=_fake_embed_deps())
        _assert_every_active_document_embedded(db)
    finally:
        db.close()
