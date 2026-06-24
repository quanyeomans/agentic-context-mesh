"""PR-2 — pre-extract compatibility gate, driven through the real pipeline.

Three scenarios, all run the production
:class:`~kairix.core.connectors.pipeline.ConnectorPipeline` built via
``kairix.core.factory.build_connector_pipeline`` (F47-compliant) so the
documents_media writer + dead-letter store + chunk writer are the wired
surfaces, not stubs:

1. **skip_path (magic-byte branch)** — an item the compat classifier
   identifies as a known-unsupported format via magic bytes (a true ZIP
   archive — ``PK\\x03\\x04`` header) is recorded on ``documents_media``
   with ``extraction_status='skipped_unsupported'``, lands ZERO chunks,
   and is NOT dead-lettered (the ``connector_deadletter`` table stays
   empty for it). The change is consumed — ``result.processed`` counts
   it.

2. **ooxml_disambiguation_path** — a docx-as-application/zip item is NOT
   skipped; the corrected MIME routes it to extraction, the extractor
   sees the corrected OOXML MIME, chunks land, and the row records
   ``extraction_status='ok'``.

3. **skip_path (MIME branch)** — a legacy binary ``application/msword``
   item with NO recognizable magic header (an OLE2 ``.doc``) is skipped
   on the MIME signal alone (the magic-byte branch never fires), proving
   the MIME-driven gate — not just the magic-byte gate — produces
   ``extraction_status='skipped_unsupported'`` end-to-end.

The extract is exercised with a recording :class:`FakeExtractor` so the
second test can assert the MIME the extractor actually received.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest

from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.integration

_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _build_pipeline(db: sqlite3.Connection, chunk_writer: FakeChunkWriter) -> Any:
    return factory.build_connector_pipeline(
        db=db,
        collection="compat-gate-test",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )


def _media_rows(db: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = db.execute(
        "SELECT hash, path, format, extraction_status, extractor_name FROM documents_media ORDER BY hash"
    )
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def _deadletter_count(db: sqlite3.Connection, item_id: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    return int(row[0])


def _true_archive_zip() -> bytes:
    """A valid ZIP of loose files — KNOWN_UNSUPPORTED (no OOXML member)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
        zf.writestr("inner/data.csv", "a,b,c")
    return buf.getvalue()


def _docx_zip() -> bytes:
    """A minimal docx — a ZIP carrying a ``word/`` member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w:document>real docx body text here</w:document>")
        zf.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


def test_known_unsupported_item_is_skipped_not_dead_lettered(tmp_path: Path) -> None:
    """A true-archive ZIP is recorded skipped_unsupported, lands no chunks, and is NOT dead-lettered."""
    db = sqlite3.connect(str(tmp_path / "skip.sqlite"))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)

    archive = _true_archive_zip()
    connector = FakeSourceConnector(
        name="sharepoint-like",
        events=[
            ChangeEvent(
                op="created",
                item_id="bundle-1",
                modified_at="2026-06-01T10:00:00Z",
                metadata={"name": "bundle.zip", "mime": "application/zip"},
            )
        ],
        content={"bundle-1": archive},
        cursor_token="cursor-skip-1",
    )

    result = pipeline.run_batch(connector, FakeExtractor())

    # Consumed (cursor advances), NOT dead-lettered.
    assert result.processed == 1
    assert result.dead_lettered == 0
    assert _deadletter_count(db, "bundle-1") == 0

    # Zero chunks landed (skip is pre-extract — upsert was never called).
    assert chunk_writer.writes == []

    # documents_media records the pre-extract skip status.
    rows = _media_rows(db)
    assert len(rows) == 1, f"expected one outcome row; got {rows!r}"
    assert rows[0]["extraction_status"] == "skipped_unsupported"
    assert rows[0]["path"] == "bundle-1"


def test_docx_as_application_zip_is_routed_to_extraction(tmp_path: Path) -> None:
    """A docx mislabeled application/zip is NOT skipped — the corrected MIME reaches the extractor."""
    db = sqlite3.connect(str(tmp_path / "docx.sqlite"))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)

    docx = _docx_zip()
    extractor = FakeExtractor()
    connector = FakeSourceConnector(
        name="sharepoint-like",
        events=[
            ChangeEvent(
                op="created",
                item_id="report-1",
                modified_at="2026-06-01T11:00:00Z",
                metadata={"name": "report", "mime": "application/zip"},
            )
        ],
        content={"report-1": docx},
        cursor_token="cursor-docx-1",
    )

    result = pipeline.run_batch(connector, extractor)

    # NOT skipped, NOT dead-lettered — it flowed end-to-end.
    assert result.processed == 1
    assert result.dead_lettered == 0
    assert _deadletter_count(db, "report-1") == 0

    # The extractor was called WITH the compat-corrected OOXML MIME, not
    # the mislabeled application/zip the connector handed over.
    assert len(extractor.extract_calls) == 1
    _called_bytes, called_mime = extractor.extract_calls[0]
    assert called_mime == _MIME_DOCX

    # Chunks landed and the row records a normal 'ok' extraction.
    assert any(len(batch) > 0 for batch in chunk_writer.writes)
    rows = _media_rows(db)
    assert len(rows) == 1
    assert rows[0]["extraction_status"] == "ok"


def test_mime_only_known_unsupported_item_is_skipped_not_dead_lettered(tmp_path: Path) -> None:
    """A legacy .doc skipped on the MIME signal alone (no magic bytes) → skipped_unsupported.

    Distinct from the magic-byte skip test above: here the payload is an
    OLE2 ``.doc`` whose leading bytes (``\\xd0\\xcf\\x11\\xe0``) are NOT
    in the classifier's magic-byte set, so the magic branch is a no-op
    and the skip MUST come from the ``application/msword`` MIME — driving
    the MIME-driven gate end-to-end through the real pipeline.
    """
    db = sqlite3.connect(str(tmp_path / "msword.sqlite"))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)

    # OLE2 / Compound File Binary header — the real legacy .doc magic,
    # which the compat classifier does NOT recognise (so no magic-byte
    # short-circuit); the skip must therefore come from the MIME.
    ole2_doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"legacy binary office payload" * 4
    connector = FakeSourceConnector(
        name="sharepoint-like",
        events=[
            ChangeEvent(
                op="created",
                item_id="legacy-1",
                modified_at="2026-06-01T12:00:00Z",
                metadata={"name": "legacy", "mime": "application/msword"},
            )
        ],
        content={"legacy-1": ole2_doc},
        mime_overrides={"legacy-1": "application/msword"},
        cursor_token="cursor-msword-1",
    )

    extractor = FakeExtractor()
    result = pipeline.run_batch(connector, extractor)

    # Consumed (cursor advances), NOT dead-lettered, extractor NEVER ran.
    assert result.processed == 1
    assert result.dead_lettered == 0
    assert _deadletter_count(db, "legacy-1") == 0
    assert extractor.extract_calls == []

    # Zero chunks landed (skip is pre-extract).
    assert chunk_writer.writes == []

    # documents_media records the pre-extract skip status — proving the
    # MIME branch (not the magic-byte branch) drove the skip.
    rows = _media_rows(db)
    assert len(rows) == 1, f"expected one outcome row; got {rows!r}"
    assert rows[0]["extraction_status"] == "skipped_unsupported"
    assert rows[0]["path"] == "legacy-1"
