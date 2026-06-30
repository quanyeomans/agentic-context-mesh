"""Page-level text access path for paged documents (PLA-270).

Page-bearing extractors (PDF / PPTX / DOCX) store the full per-page text in
``document_pages.extracted_text`` at ingest, but that text was **unreachable
after retrieval** — a search hit could cite a page number (``source_page``)
yet an agent had no way to pull the page's full text back. This module is
that missing read path: given a hit's canonical ``source_uri`` and its
``source_page``, return the stored page text.

Linkage (the keys differ across the three tables, so the join is non-obvious):

* ``documents.source_uri`` — the hit's canonical breadcrumb (PLA-274).
* ``silver_source(source_uri, hash)`` — bridges the document's ``source_uri``
  to the **raw-bytes** content hash (``documents_media.hash``), persisted at
  ingest for the re-chunk sweep (ADR-028 Wave F.4).
* ``document_pages(hash, page_number, extracted_text)`` — keyed by that same
  raw-bytes hash.

So ``source_uri → silver_source.hash → document_pages`` (filtered by
``page_number``) yields the page text. This is the public surface the
chunk-expansion tool (PLA-268) consumes to expand a paged hit to its full
page.

All functions return ``None`` on a miss (non-paged doc, page not stored, or
a document ingested before ``silver_source`` was populated) and never raise —
matching the search tier's "degrade, don't blow up" contract.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# F63-bounded: the (source_uri, page_number) pair resolves to at most one
# stored page row; ``LIMIT 1`` makes that explicit and bounds the scan.
_PAGE_TEXT_SQL = """
    SELECT dp.extracted_text
    FROM silver_source ss
    JOIN document_pages dp ON dp.hash = ss.hash
    WHERE ss.source_uri = ? AND dp.page_number = ?
    LIMIT 1
"""


def page_text(*, source_uri: str, page_number: int, db: sqlite3.Connection) -> str | None:
    """Return the full extracted text for one page of a paged document.

    The connection-injected core — the chunk-expansion tool (PLA-268) calls
    this with the search pipeline's open DB handle so no second connection is
    opened on the hot path.

    Args:
        source_uri:  The hit's canonical breadcrumb (``documents.source_uri``
                     / ``SearchHit.source_uri``).
        page_number: The hit's ``source_page`` (1-based page / slide / sheet).
        db:          An open ``sqlite3.Connection`` to the worker index.

    Returns:
        The page's ``extracted_text``, or ``None`` when no page is stored for
        that ``(source_uri, page_number)`` — a non-paged document, an
        out-of-range page, a NULL-text page, or a document ingested before
        ``silver_source`` carried its bridge row. Never raises.
    """
    if not source_uri:
        return None
    try:
        row = db.execute(_PAGE_TEXT_SQL, (source_uri, page_number)).fetchone()
    except Exception as exc:
        # Search tier degrades, never raises — a malformed/absent schema
        # surfaces as a miss, not a crash on the agent's read path.
        logger.warning("page_text: lookup failed for source_uri=%r page=%d — %s", source_uri, page_number, exc)
        return None
    if row is None or row[0] is None:
        return None
    return str(row[0])


def lookup_page_text(*, source_uri: str, page_number: int, db_path: Path | None = None) -> str | None:
    """Open the worker index and return one page's text (production convenience).

    Thin wrapper around :func:`page_text` that resolves + opens the default
    worker database (``db_path=None``) via the shared ``open_db`` helper, so
    callers without an existing handle (a CLI / MCP expand surface that runs
    outside the search pipeline) can reach a page directly. Closes the
    connection it opens. Returns ``None`` on any open/lookup failure.
    """
    from kairix.core.db import open_db

    try:
        db = open_db(db_path)
    except Exception as exc:
        # Degrade rather than raise on a missing / locked / unopenable DB.
        logger.warning("lookup_page_text: cannot open database — %s", exc)
        return None
    try:
        return page_text(source_uri=source_uri, page_number=page_number, db=db)
    finally:
        db.close()
