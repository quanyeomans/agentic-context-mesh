"""
Integration tests: temporal index retrieval against synthetic agent memory logs.
"""

from datetime import date

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_timeline_finds_memory_logs(real_db, real_document_root):
    """Timeline retrieval finds agent memory logs within date range."""
    from kairix.core.temporal.index import query_temporal_chunks

    results = query_temporal_chunks(
        "session update",
        start=date(2026, 4, 28),
        end=date(2026, 4, 30),
    )
    assert len(results) > 0


@pytest.mark.integration
def test_timeline_returns_empty_for_future_dates(real_db, real_document_root):
    """Timeline returns empty for dates with no memory logs."""
    from kairix.core.temporal.index import query_temporal_chunks

    results = query_temporal_chunks(
        "anything",
        start=date(2099, 1, 1),
        end=date(2099, 12, 31),
    )
    assert len(results) == 0
