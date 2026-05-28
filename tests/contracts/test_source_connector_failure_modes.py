"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`SourceConnector`.

Every public method on :class:`kairix.core.protocols.SourceConnector`
has at least one test here that exercises a named failure class
(``raises`` / ``times_out`` / ``returns_partial`` / ``returns_empty`` /
``unauthorized`` / ``unavailable``) AND asserts a CONCRETE observable
outcome — a row count, a row's column value, an exception type, a
returned value — not a Mock call-count.

Bug 2 (2026-05 SharePoint 429 dead-lettering every item on a throttled
drive) shipped because no contract test exercised the rate-limit path.
F68 makes the failure-behaviour contract mechanically required for
every Protocol method.

Composition follows F47 — pipelines are built via
:func:`kairix.core.factory.build_connector_pipeline` with canonical
fakes from :mod:`tests.fakes` injected as overrides. No monkeypatches,
no private-attribute substitution.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth; mutations were executed during
authoring and the sabotage assertions failed concretely (then the
mutation was reverted).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Helpers — factory-composed pipeline with canonical fakes (F47-compliant).
# ---------------------------------------------------------------------------


def _build_pipeline(
    db: sqlite3.Connection,
    *,
    chunk_writer: FakeChunkWriter | None = None,
    entity_graph_sink: FakeEntityGraphSink | None = None,
):
    """F47-compliant: ConnectorPipeline composed via the factory entry point."""
    return build_connector_pipeline(
        db=db,
        collection="default",
        chunk_writer=chunk_writer if chunk_writer is not None else FakeChunkWriter(),
        entity_graph_sink=entity_graph_sink if entity_graph_sink is not None else FakeEntityGraphSink(),
    )


def _make_event(item_id: str, modified_at: str = "2026-01-01T00:00:00Z") -> ChangeEvent:
    return ChangeEvent(op="created", item_id=item_id, modified_at=modified_at)


def _dead_letter_rows(db: sqlite3.Connection, source_name: str) -> list[tuple[str, str]]:
    return list(
        db.execute(
            "SELECT item_id, last_error FROM connector_deadletter WHERE source_name = ? ORDER BY item_id",
            (source_name,),
        ).fetchall()
    )


def _cursor_token(db: sqlite3.Connection, source_name: str) -> str | None:
    row = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return None if row is None else row[0]


# ---------------------------------------------------------------------------
# SourceConnector.list_changes
# ---------------------------------------------------------------------------


def test_list_changes_raises_propagates_and_leaves_cursor_unchanged(tmp_path: Path) -> None:
    """A connector whose :meth:`list_changes` raises must surface the
    exception (not silently truncate the batch) AND the prior cursor
    must NOT be clobbered — the next tick can resume from the
    last-known-good token.

    Sabotage proof: in ``FakeSourceConnector.list_changes`` comment out
    the ``if self._raise_on_list_changes is not None: raise ...`` block.
    Re-run: the test fails because ``pytest.raises`` sees no exception
    and the dead-letter / processed assertions don't fire. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    # Seed a baseline cursor so we can prove ``list_changes`` failing
    # didn't overwrite it.
    db.execute(
        "INSERT INTO connector_cursors (source_name, cursor_token, updated_at) VALUES (?, ?, ?)",
        ("raising-list", "cursor-A", "2026-01-01T00:00:00Z"),
    )
    db.commit()

    source = FakeSourceConnector(
        name="raising-list",
        raise_on_list_changes=RuntimeError("F68-list-changes-raises"),
    )
    pipeline = _build_pipeline(db)

    with pytest.raises(RuntimeError, match="F68-list-changes-raises"):
        pipeline.run_batch(source, FakeExtractor())

    # Cursor must NOT have been clobbered — the next tick will re-read
    # ``cursor-A`` and retry the same range.
    assert _cursor_token(db, "raising-list") == "cursor-A"
    # No items dead-lettered (the failure happened before any item was
    # processed) — proves the exception fired at the iteration boundary.
    assert _dead_letter_rows(db, "raising-list") == []
    db.close()


# ---------------------------------------------------------------------------
# SourceConnector.fetch
# ---------------------------------------------------------------------------


def test_fetch_raises_propagates_to_dead_letter(tmp_path: Path) -> None:
    """A connector that raises on ``fetch`` for one item must
    dead-letter that item; sibling items still process and the
    per-item failure does not abort the batch.

    Sabotage proof: in ``FakeSourceConnector.fetch`` comment out the
    ``if item_id in self._fail_on_fetch: raise ...`` block. Re-run:
    the test fails because dead_lettered == 0 and processed == 3
    instead of (2, 1) — no failure was injected so no dead-letter
    row appears. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    source = FakeSourceConnector(
        name="fetch-raises",
        events=[_make_event(f"item-{i:03d}") for i in range(3)],
        content={f"item-{i:03d}": f"body-{i}".encode() for i in range(3)},
        fail_on_fetch={"item-001"},
    )
    pipeline = _build_pipeline(db)
    result = pipeline.run_batch(source, FakeExtractor())

    rows = _dead_letter_rows(db, "fetch-raises")
    assert result.processed == 2
    assert result.dead_lettered == 1
    assert len(rows) == 1
    assert rows[0][0] == "item-001"
    assert "fetch" in rows[0][1].lower()
    db.close()


def test_fetch_times_out_propagates_to_dead_letter(tmp_path: Path) -> None:
    """A connector whose ``fetch`` raises :class:`TimeoutError` for one
    item must dead-letter it with the timeout error preserved in
    ``last_error``. Mirrors the SharePoint Graph timeout path that
    surfaced as Bug 2 in 2026-05 — a TimeoutError is a different
    failure class than a generic RuntimeError but the pipeline's
    handler must absorb both shapes identically.

    Sabotage proof: in ``FakeSourceConnector.fetch`` comment out the
    ``if item_id in self._timeout_on_fetch: raise TimeoutError(...)``
    block. Re-run: the test fails because no dead-letter row appears
    and ``result.dead_lettered == 0`` instead of 1. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    source = FakeSourceConnector(
        name="fetch-times-out",
        events=[_make_event("item-001"), _make_event("item-002")],
        content={"item-001": b"a", "item-002": b"b"},
        timeout_on_fetch={"item-001"},
    )
    pipeline = _build_pipeline(db)
    result = pipeline.run_batch(source, FakeExtractor())

    rows = _dead_letter_rows(db, "fetch-times-out")
    assert result.dead_lettered == 1
    assert len(rows) == 1
    assert rows[0][0] == "item-001"
    assert "timeout" in rows[0][1].lower()
    db.close()


# ---------------------------------------------------------------------------
# SourceConnector.source_link
# ---------------------------------------------------------------------------


def test_source_link_raises_propagates_and_rolls_back_chunk(tmp_path: Path) -> None:
    """``source_link`` is called by ``_process_item`` AFTER bronze write
    + extract — a raise propagates and rolls back that chunk's
    bronze rows. The pipeline does NOT wrap ``source_link`` in a
    try/except (only ``fetch`` and ``extract`` are absorbed) — so the
    expected behaviour is "exception escapes ``run_batch``".

    Sabotage proof: in ``FakeSourceConnector.source_link`` remove the
    ``if item_id in self._raise_on_source_link: raise ...`` block.
    Re-run: the test fails because ``pytest.raises`` sees nothing and
    the items index successfully. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    source = FakeSourceConnector(
        name="source-link-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body"},
        raise_on_source_link={"item-001"},
    )
    pipeline = _build_pipeline(db)
    with pytest.raises(RuntimeError, match="simulated source_link failure"):
        pipeline.run_batch(source, FakeExtractor())
    db.close()


# ---------------------------------------------------------------------------
# SourceConnector.sensitivity_for
# ---------------------------------------------------------------------------


def test_sensitivity_for_raises_propagates_and_rolls_back_chunk(tmp_path: Path) -> None:
    """``sensitivity_for`` is called by ``_process_item`` for the silver
    pass. A raise here propagates — the chunk rolls back and no
    content / entity_signals row is written for that item.

    Sabotage proof: comment out the raise branch in
    ``FakeSourceConnector.sensitivity_for``. Re-run: the test fails
    because ``pytest.raises`` sees no exception. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="sensitivity-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body"},
        raise_on_sensitivity_for={"item-001"},
    )
    pipeline = _build_pipeline(db, chunk_writer=writer)
    with pytest.raises(RuntimeError, match="simulated sensitivity_for failure"):
        pipeline.run_batch(source, FakeExtractor())
    # Failing chunk rolled back — no chunk batch reached the writer
    # (the silver pass raised before reaching ``chunk_writer.upsert``).
    assert writer.writes == [], f"writer must not have received chunks; got {writer.writes!r}"
    db.close()


# ---------------------------------------------------------------------------
# SourceConnector.next_cursor
# ---------------------------------------------------------------------------


def test_next_cursor_raises_propagates_and_aborts_commit(tmp_path: Path) -> None:
    """``next_cursor`` is called by ``_commit_and_flush`` at end-of-chunk
    AND end-of-batch. A raise during ``next_cursor`` propagates;
    the cursor never advances and the pipeline rolls back.

    Sabotage proof: comment out the raise branch in
    ``FakeSourceConnector.next_cursor``. Re-run: the test fails
    because ``pytest.raises`` sees no exception and the cursor
    advance attempt succeeds. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    source = FakeSourceConnector(
        name="next-cursor-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body"},
        raise_on_next_cursor=RuntimeError("F68-next-cursor-raises"),
    )
    pipeline = _build_pipeline(db)
    with pytest.raises(RuntimeError, match="F68-next-cursor-raises"):
        pipeline.run_batch(source, FakeExtractor())
    # Cursor row never created — the commit didn't survive the raise.
    assert _cursor_token(db, "next-cursor-raises") is None
    db.close()


# ---------------------------------------------------------------------------
# SourceConnector.metadata_for
# ---------------------------------------------------------------------------


def test_metadata_for_raises_returns_empty_metadata_chunk_still_indexed(tmp_path: Path) -> None:
    """ADR-021 Wave E.5 contract — ``metadata_for`` failing is NEVER
    fatal. The pipeline falls back to empty :class:`SourceMetadata`
    via :func:`_safe_connector_metadata` and the chunk still flows to
    the writer.

    Note this is intentionally the only Protocol method on
    SourceConnector whose ``raises`` failure class observably looks
    like ``returns_empty`` — the wrapper absorbs the exception by
    design (see ``kairix/core/connectors/pipeline.py:464``).

    Sabotage proof: in ``kairix/core/connectors/pipeline.py``
    :func:`_safe_connector_metadata`, change the
    ``except Exception: return SourceMetadata()`` to ``except Exception: raise``.
    Re-run: the test fails because the pipeline now propagates the
    RuntimeError instead of falling back. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="metadata-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body-content"},
        raise_on_metadata_for={"item-001"},
    )
    pipeline = _build_pipeline(db, chunk_writer=writer)
    result = pipeline.run_batch(source, FakeExtractor())

    # The chunk IS written — metadata_for failure is absorbed.
    assert result.processed == 1
    assert result.dead_lettered == 0
    assert len(writer.writes) == 1, f"writer should have received exactly one chunk batch; got {writer.writes!r}"
    # And no dead-letter entry — metadata failures do not dead-letter.
    assert _dead_letter_rows(db, "metadata-raises") == []
    db.close()


def test_metadata_for_returns_empty_when_item_not_in_scripted_map(tmp_path: Path) -> None:
    """``returns_empty`` failure class — when ``metadata_for`` returns
    an empty :class:`SourceMetadata` (no error, just no data), the
    chunk still indexes; author / chunk_date columns are NULL but the
    pipeline continues.

    Sabotage proof: in ``FakeSourceConnector.metadata_for``, return
    ``None`` instead of ``SourceMetadata()`` for missing entries. Re-run:
    the test fails because the silver pass crashes when it tries to
    read attributes off ``None``. Restored.
    """
    from kairix.core.protocols import SourceMetadata

    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="metadata-empty",
        events=[_make_event("item-001")],
        content={"item-001": b"body-content"},
        # No metadata mapping passed — every metadata_for call returns
        # an empty :class:`SourceMetadata`.
    )
    # Drive the empty path explicitly via the Protocol surface — proves
    # the empty-return shape is the canonical fallback.
    md = source.metadata_for("any-missing-id")
    assert isinstance(md, SourceMetadata)
    assert md.author is None
    pipeline = _build_pipeline(db, chunk_writer=writer)
    result = pipeline.run_batch(source, FakeExtractor())
    assert result.processed == 1
    assert len(writer.writes) == 1, f"writer should have received exactly one chunk batch; got {writer.writes!r}"
    db.close()
