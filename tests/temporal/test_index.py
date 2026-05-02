"""
Tests for kairix.core.temporal.index — query_temporal_chunks and get_memory_log_paths.

Uses document_root DI parameter to pass temp dirs directly (no patching needed).
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from kairix.core.temporal.index import get_memory_log_paths, query_temporal_chunks


@pytest.fixture()
def doc_root(tmp_path: Path) -> Path:
    """Create a synthetic document root with agent memory logs and boards."""
    # Agent memory logs
    memory_dir = tmp_path / "04-Agent-Knowledge" / "builder" / "memory"
    memory_dir.mkdir(parents=True)

    (memory_dir / "2026-04-28.md").write_text(
        textwrap.dedent("""\
            ## Session Summary

            Worked on hybrid search integration and BM25 tuning.

            ## Decisions

            - Adopted RRF fusion strategy for search.
        """),
        encoding="utf-8",
    )
    (memory_dir / "2026-04-29.md").write_text(
        textwrap.dedent("""\
            ## Session Summary

            Completed temporal index implementation.

            ## Next Steps

            - Run benchmark after Phase 3.
        """),
        encoding="utf-8",
    )
    (memory_dir / "2026-03-15.md").write_text(
        textwrap.dedent("""\
            ## Session Summary

            Old session from March.
        """),
        encoding="utf-8",
    )

    # Board files
    boards_dir = tmp_path / "01-Projects" / "Boards"
    boards_dir.mkdir(parents=True)
    (boards_dir / "Kairix.md").write_text(
        textwrap.dedent("""\
            ## Done

            - [ ] Phase 1 shipped [completed::2026-03-10] [project::Kairix]

            ## In Progress

            - [ ] Phase 3 temporal [started::2026-04-28] [project::Kairix]
        """),
        encoding="utf-8",
    )

    return tmp_path


@pytest.mark.unit
class TestGetMemoryLogPaths:
    @pytest.mark.unit
    def test_finds_logs_in_date_range(self, doc_root: Path) -> None:
        paths = get_memory_log_paths(
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            document_root=doc_root,
        )
        assert len(paths) == 2
        assert any("2026-04-28.md" in p for p in paths)
        assert any("2026-04-29.md" in p for p in paths)

    @pytest.mark.unit
    def test_excludes_logs_outside_range(self, doc_root: Path) -> None:
        paths = get_memory_log_paths(
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            document_root=doc_root,
        )
        assert not any("2026-03-15.md" in p for p in paths)

    @pytest.mark.unit
    def test_returns_all_when_no_range(self, doc_root: Path) -> None:
        paths = get_memory_log_paths(start=None, end=None, document_root=doc_root)
        assert len(paths) == 3

    @pytest.mark.unit
    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        paths = get_memory_log_paths(
            start=None,
            end=None,
            document_root=tmp_path,
        )
        assert paths == []

    @pytest.mark.unit
    def test_returns_sorted_paths(self, doc_root: Path) -> None:
        paths = get_memory_log_paths(start=None, end=None, document_root=doc_root)
        assert paths == sorted(paths)


@pytest.mark.unit
class TestQueryTemporalChunks:
    @pytest.mark.unit
    def test_finds_chunks_matching_topic(self, doc_root: Path) -> None:
        results = query_temporal_chunks(
            topic="hybrid search",
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            document_root=doc_root,
        )
        assert len(results) > 0
        assert any("hybrid" in c.text.lower() or "search" in c.text.lower() for c in results)

    @pytest.mark.unit
    def test_returns_empty_for_future_dates(self, doc_root: Path) -> None:
        results = query_temporal_chunks(
            topic="anything",
            start=date(2099, 1, 1),
            end=date(2099, 12, 31),
            document_root=doc_root,
        )
        assert len(results) == 0

    @pytest.mark.unit
    def test_filters_by_chunk_type(self, doc_root: Path) -> None:
        results = query_temporal_chunks(
            topic="Phase",
            start=None,
            end=None,
            chunk_types=["memory_section"],
            document_root=doc_root,
        )
        assert all(c.chunk_type == "memory_section" for c in results)

    @pytest.mark.unit
    def test_respects_limit(self, doc_root: Path) -> None:
        results = query_temporal_chunks(
            topic="session",
            start=None,
            end=None,
            limit=1,
            document_root=doc_root,
        )
        assert len(results) <= 1

    @pytest.mark.unit
    def test_returns_empty_for_empty_dir(self, tmp_path: Path) -> None:
        results = query_temporal_chunks(
            topic="anything",
            start=None,
            end=None,
            document_root=tmp_path,
        )
        assert results == []
