"""
Integration tests: document scanner indexing and content deduplication.
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_scanner_indexes_documents(real_db, real_document_root):
    """Scanner finds and indexes documents from the fixture."""
    count = real_db.execute("SELECT count(*) FROM documents WHERE active=1").fetchone()[0]
    assert count > 30  # At least the 31 reflib fixture docs


@pytest.mark.integration
def test_scanner_no_duplicate_content(real_db, real_document_root):
    """No two active documents have the same content hash."""
    dupes = real_db.execute(
        "SELECT hash, count(*) as n FROM documents WHERE active=1 GROUP BY hash HAVING n > 1"
    ).fetchall()
    assert len(dupes) == 0, f"Found {len(dupes)} duplicate content hashes"
