"""
Tests for kairix.core.temporal.chunker.

Covers:
  - chunk_board(): column parsing, date extraction, cards without dates
  - chunk_memory_log(): date from filename, section splitting, frontmatter strip
  - chunk_file(): dispatch to board vs memory chunker
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from kairix.core.temporal.chunker import chunk_board, chunk_file, chunk_memory_log

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def board_file(tmp_path: Path) -> Path:
    """Create a minimal Kanban board file for testing."""
    content = textwrap.dedent("""\
        ---

        kanban-plugin: board

        ---

        ## Done

        - [ ] Phase 1 shipped [completed::2026-03-10] [project::Kairix]
        - [ ] Fix BM25 bug [completed::2026-03-12] [started::2026-03-11]

        ## In Progress

        - [ ] Phase 2 temporal [started::2026-03-23] [project::Kairix]

        ## Ready

        - [ ] Seed entities.db [created::2026-03-23]

        ## Backlog

        - [ ] Future task with no dates at all

    """)
    p = tmp_path / "Boards" / "Kairix.md"
    p.parent.mkdir(parents=True)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def memory_log_file(tmp_path: Path) -> Path:
    """Create a memory log file named 2026-03-22.md."""
    content = textwrap.dedent("""\
        ---
        date: 2026-03-22
        ---

        ## Session Summary

        Worked on hybrid search integration.

        ## Decisions

        - Use BM25 + vector fusion.
        - Adopt RRF as the fusion strategy.

        ## Next Steps

        - Run benchmark after Phase 2.
    """)
    p = tmp_path / "memory" / "2026-03-22.md"
    p.parent.mkdir(parents=True)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def undated_memory_log(tmp_path: Path) -> Path:
    """Memory log with no ## headings and no date in filename."""
    content = "Some raw notes without structure.\nMore notes here."
    p = tmp_path / "notes.md"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def board_no_dates(tmp_path: Path) -> Path:
    """Board with cards that have no date fields."""
    content = textwrap.dedent("""\
        ## Done

        - [ ] Task with no date tags

        ## Backlog

        - [ ] Another undated task

    """)
    p = tmp_path / "Boards" / "Plain.md"
    p.parent.mkdir(parents=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# chunk_board tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChunkBoard:
    @pytest.mark.unit
    def test_parses_all_columns(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        assert len(chunks) > 0

    @pytest.mark.unit
    def test_chunk_type_is_board_card(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        assert all(c.chunk_type == "board_card" for c in chunks)

    @pytest.mark.unit
    def test_source_path_preserved(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        assert all(c.source_path == str(board_file) for c in chunks)

    @pytest.mark.unit
    def test_extracts_completed_date(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        # "Phase 1 shipped" card should have completed date 2026-03-10
        phase1 = [c for c in chunks if "Phase 1 shipped" in c.text]
        assert phase1, "Expected to find 'Phase 1 shipped' card"
        assert phase1[0].date == date(2026, 3, 10)
        assert phase1[0].metadata.get("date_field") == "completed"

    @pytest.mark.unit
    def test_extracts_started_date_when_no_completed(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        # "Phase 2 temporal" card has only [started::2026-03-23]
        phase2 = [c for c in chunks if "Phase 2 temporal" in c.text]
        assert phase2
        assert phase2[0].date == date(2026, 3, 23)
        assert phase2[0].metadata.get("date_field") == "started"

    @pytest.mark.unit
    def test_completed_takes_priority_over_started(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        # "Fix BM25 bug" has both [completed::2026-03-12] and [started::2026-03-11]
        bug = [c for c in chunks if "Fix BM25 bug" in c.text]
        assert bug
        assert bug[0].date == date(2026, 3, 12), "completed should take priority over started"

    @pytest.mark.unit
    def test_cards_without_dates_get_date_none(self, board_no_dates: Path) -> None:
        chunks = chunk_board(str(board_no_dates))
        assert len(chunks) > 0
        undated = [c for c in chunks if c.date is None]
        assert len(undated) == len(chunks), "All cards should have date=None when no date tags"

    @pytest.mark.unit
    def test_cards_without_dates_have_status(self, board_no_dates: Path) -> None:
        chunks = chunk_board(str(board_no_dates))
        done_chunks = [c for c in chunks if c.metadata.get("status") == "done"]
        assert done_chunks, "Done column cards should have status='done'"

    @pytest.mark.unit
    def test_column_status_mapping(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        # In Progress column → status="in_progress"
        in_progress = [c for c in chunks if "Phase 2 temporal" in c.text]
        assert in_progress
        assert in_progress[0].metadata.get("status") == "in_progress"

    @pytest.mark.unit
    def test_card_id_is_set(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        assert all("card_id" in c.metadata for c in chunks)

    @pytest.mark.unit
    def test_returns_empty_on_missing_file(self) -> None:
        chunks = chunk_board("/nonexistent/path/board.md")
        assert chunks == []

    @pytest.mark.unit
    def test_created_date_used_as_fallback(self, board_file: Path) -> None:
        chunks = chunk_board(str(board_file))
        # "Seed entities.db" has [created::2026-03-23] only
        seed = [c for c in chunks if "Seed entities" in c.text]
        assert seed
        assert seed[0].date == date(2026, 3, 23)
        assert seed[0].metadata.get("date_field") == "created"


# ---------------------------------------------------------------------------
# chunk_memory_log tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChunkMemoryLog:
    @pytest.mark.unit
    def test_date_extracted_from_filename(self, memory_log_file: Path) -> None:
        chunks = chunk_memory_log(str(memory_log_file))
        assert len(chunks) > 0
        assert all(c.date == date(2026, 3, 22) for c in chunks)

    @pytest.mark.unit
    def test_splits_on_section_headers(self, memory_log_file: Path) -> None:
        chunks = chunk_memory_log(str(memory_log_file))
        headings = [c.metadata.get("section_heading") for c in chunks]
        assert "Session Summary" in headings
        assert "Decisions" in headings
        assert "Next Steps" in headings

    @pytest.mark.unit
    def test_chunk_type_is_memory_section(self, memory_log_file: Path) -> None:
        chunks = chunk_memory_log(str(memory_log_file))
        assert all(c.chunk_type == "memory_section" for c in chunks)

    @pytest.mark.unit
    def test_source_path_preserved(self, memory_log_file: Path) -> None:
        chunks = chunk_memory_log(str(memory_log_file))
        assert all(c.source_path == str(memory_log_file) for c in chunks)

    @pytest.mark.unit
    def test_frontmatter_stripped(self, memory_log_file: Path) -> None:
        chunks = chunk_memory_log(str(memory_log_file))
        # Frontmatter "date: 2026-03-22" should not appear in chunk text
        all_text = " ".join(c.text for c in chunks)
        assert "kanban-plugin" not in all_text

    @pytest.mark.unit
    def test_section_text_contains_content(self, memory_log_file: Path) -> None:
        chunks = chunk_memory_log(str(memory_log_file))
        decisions = [c for c in chunks if c.metadata.get("section_heading") == "Decisions"]
        assert decisions
        assert "BM25" in decisions[0].text or "RRF" in decisions[0].text

    @pytest.mark.unit
    def test_no_headings_produces_single_chunk(self, undated_memory_log: Path) -> None:
        chunks = chunk_memory_log(str(undated_memory_log))
        assert len(chunks) == 1
        assert "raw notes" in chunks[0].text

    @pytest.mark.unit
    def test_invalid_filename_gives_none_date(self, tmp_path: Path) -> None:
        p = tmp_path / "random_notes.md"
        p.write_text("## Notes\n\nSome content.", encoding="utf-8")
        chunks = chunk_memory_log(str(p))
        assert all(c.date is None for c in chunks)

    @pytest.mark.unit
    def test_returns_empty_on_missing_file(self) -> None:
        chunks = chunk_memory_log("/nonexistent/path/2026-03-22.md")
        assert chunks == []


# ---------------------------------------------------------------------------
# chunk_file dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChunkFile:
    @pytest.mark.unit
    def test_dispatches_memory_log_by_filename(self, memory_log_file: Path) -> None:
        chunks = chunk_file(str(memory_log_file))
        assert len(chunks) > 0
        assert all(c.chunk_type == "memory_section" for c in chunks)

    @pytest.mark.unit
    def test_dispatches_board_by_directory(self, board_file: Path) -> None:
        chunks = chunk_file(str(board_file))
        assert len(chunks) > 0
        assert all(c.chunk_type == "board_card" for c in chunks)

    @pytest.mark.unit
    def test_board_detected_by_content(self, tmp_path: Path) -> None:
        """File not in Boards/ dir but with kanban content should be detected as board."""
        content = textwrap.dedent("""\
            ## Done

            - [ ] Some task [completed::2026-03-10]

            ## Backlog

            - [ ] Future work
        """)
        p = tmp_path / "project-status.md"
        p.write_text(content, encoding="utf-8")
        chunks = chunk_file(str(p))
        # Content has ## Done so should be detected as board
        assert any(c.chunk_type == "board_card" for c in chunks)

    @pytest.mark.unit
    def test_memory_filename_takes_priority(self, tmp_path: Path) -> None:
        """YYYY-MM-DD.md should always be treated as memory log."""
        content = textwrap.dedent("""\
            ## Done

            - [ ] Task [completed::2026-03-10]
        """)
        p = tmp_path / "2026-03-22.md"
        p.write_text(content, encoding="utf-8")
        chunks = chunk_file(str(p))
        # Even though content looks like a board, filename wins
        assert all(c.chunk_type == "memory_section" for c in chunks)
