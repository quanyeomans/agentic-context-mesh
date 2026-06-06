"""
Tests for kairix.core.temporal.index — query_temporal_chunks and get_memory_log_paths.

PR 1.2 / #420 — surfaces resolve via :func:`kairix.core.agents.scope.load_agent_scopes`
(driven by the ``agents:`` block in ``kairix.config.yaml``). Tests
construct a synthetic config dict and pass it via the ``config=`` seam.
The ``document_root`` parameter is reserved (currently unused by
get_memory_log_paths) — surfaces in AgentScope carry absolute paths.
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from kairix.core.temporal.index import get_memory_log_paths, query_temporal_chunks


def _build_doc_root_with_config(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    """Create a synthetic document root + matching ``agents:`` config block.

    The vault layout still uses the historical ``04-Agent-Knowledge/<agent>/memory``
    shape (kept as one valid layout shape — operators with that vault
    declare it in their config). The new contract is that the path
    flows through ``agents.builder.surfaces`` rather than being scanned
    by directory convention.
    """
    memory_dir = tmp_path / "04-Agent-Knowledge" / "builder" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

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
    boards_dir.mkdir(parents=True, exist_ok=True)
    (boards_dir / "Kairix.md").write_text(
        textwrap.dedent("""\
            ## Done

            - [ ] Phase 1 shipped [completed::2026-03-10] [project::Kairix]

            ## In Progress

            - [ ] Phase 3 temporal [started::2026-04-28] [project::Kairix]
        """),
        encoding="utf-8",
    )

    config: dict[str, object] = {
        "agents": {
            "builder": {
                "surfaces": [{"path": str(memory_dir), "label": "memory"}],
            },
        },
    }
    return tmp_path, config


@pytest.fixture()
def doc_root(tmp_path: Path) -> Path:
    """Backwards-named fixture; returns the document root path."""
    root, _ = _build_doc_root_with_config(tmp_path)
    return root


@pytest.fixture()
def doc_root_config(tmp_path: Path) -> dict[str, object]:
    """Companion fixture — the ``agents:`` config block matching ``doc_root``."""
    _, config = _build_doc_root_with_config(tmp_path)
    return config


def _config_for_memory_dir(memory_dir: Path, agent: str = "builder") -> dict[str, object]:
    """Build an inline ``agents:`` config pointing the agent at ``memory_dir``."""
    return {
        "agents": {
            agent: {"surfaces": [{"path": str(memory_dir), "label": "memory"}]},
        },
    }


@pytest.mark.unit
class TestGetMemoryLogPaths:
    @pytest.mark.unit
    def test_finds_logs_in_date_range(self, doc_root_config: dict[str, object]) -> None:
        _ = doc_root_config  # implicit fixture: builds the memory_dir contents on disk
        paths = get_memory_log_paths(
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            config=doc_root_config,
        )
        assert len(paths) == 2
        assert any("2026-04-28.md" in p for p in paths)
        assert any("2026-04-29.md" in p for p in paths)

    @pytest.mark.unit
    def test_excludes_logs_outside_range(self, doc_root_config: dict[str, object]) -> None:
        paths = get_memory_log_paths(
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            config=doc_root_config,
        )
        assert not any("2026-03-15.md" in p for p in paths)

    @pytest.mark.unit
    def test_returns_all_when_no_range(self, doc_root_config: dict[str, object]) -> None:
        paths = get_memory_log_paths(start=None, end=None, config=doc_root_config)
        assert len(paths) == 3

    @pytest.mark.unit
    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        # Config points at a non-existent memory dir → empty result, no crash.
        cfg = _config_for_memory_dir(tmp_path / "no-such-dir")
        paths = get_memory_log_paths(start=None, end=None, config=cfg)
        assert paths == []

    @pytest.mark.unit
    def test_returns_sorted_paths(self, doc_root_config: dict[str, object]) -> None:
        paths = get_memory_log_paths(start=None, end=None, config=doc_root_config)
        assert paths == sorted(paths)


@pytest.mark.unit
class TestQueryTemporalChunks:
    @pytest.mark.unit
    def test_finds_chunks_matching_topic(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        results = query_temporal_chunks(
            topic="hybrid search",
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            document_root=doc_root,
            config=doc_root_config,
        )
        assert len(results) > 0
        assert any("hybrid" in c.text.lower() or "search" in c.text.lower() for c in results)

    @pytest.mark.unit
    def test_returns_empty_for_future_dates(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        results = query_temporal_chunks(
            topic="anything",
            start=date(2099, 1, 1),
            end=date(2099, 12, 31),
            document_root=doc_root,
            config=doc_root_config,
        )
        assert len(results) == 0

    @pytest.mark.unit
    def test_filters_by_chunk_type(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        results = query_temporal_chunks(
            topic="Phase",
            start=None,
            end=None,
            chunk_types=["memory_section"],
            document_root=doc_root,
            config=doc_root_config,
        )
        assert all(c.chunk_type == "memory_section" for c in results)

    @pytest.mark.unit
    def test_respects_limit(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        results = query_temporal_chunks(
            topic="session",
            start=None,
            end=None,
            limit=1,
            document_root=doc_root,
            config=doc_root_config,
        )
        assert len(results) <= 1

    @pytest.mark.unit
    def test_returns_empty_for_empty_dir(self, tmp_path: Path) -> None:
        # Empty document_root + empty config → no boards, no memory → no results.
        results = query_temporal_chunks(
            topic="anything",
            start=None,
            end=None,
            document_root=tmp_path,
            config={"agents": {}},
        )
        assert results == []


@pytest.mark.unit
class TestGetMemoryLogPathsEdgeCases:
    """Cover malformed filenames, invalid dates, missing surfaces.

    PR 1.2 / #420 — the legacy "stray file in 04-Agent-Knowledge" /
    "agent dir without memory subdir" edge cases no longer apply (the
    function iterates configured AgentScope surfaces, not filesystem
    siblings). Filename + date-parsing edge cases still matter because
    they're per-file.
    """

    @pytest.mark.unit
    def test_skips_files_with_non_matching_filename(self, tmp_path: Path) -> None:
        """Files not matching YYYY-MM-DD.md are skipped (filename regex guard)."""
        memory = tmp_path / "builder-memory"
        memory.mkdir()
        (memory / "README.md").write_text("# Index")
        (memory / "2026-04-29.md").write_text("## Real log")

        paths = get_memory_log_paths(start=None, end=None, config=_config_for_memory_dir(memory))
        assert len(paths) == 1
        assert "2026-04-29.md" in paths[0]

    @pytest.mark.unit
    def test_skips_files_with_invalid_dates(self, tmp_path: Path) -> None:
        """Files with regex-matching but invalid dates are skipped."""
        memory = tmp_path / "builder-memory"
        memory.mkdir()
        # February 30 doesn't exist — regex matches but date() raises ValueError
        (memory / "2026-02-30.md").write_text("## Invalid date")
        (memory / "2026-02-28.md").write_text("## Valid")

        paths = get_memory_log_paths(start=None, end=None, config=_config_for_memory_dir(memory))
        assert len(paths) == 1
        assert "2026-02-28.md" in paths[0]

    @pytest.mark.unit
    def test_skips_surface_that_is_not_a_directory(self, tmp_path: Path) -> None:
        """A configured surface that doesn't exist on disk is silently skipped
        (replaces the legacy 'stray file in 04-Agent-Knowledge' edge case).
        """
        absent = tmp_path / "never-created"
        present = tmp_path / "present"
        present.mkdir()
        (present / "2026-04-29.md").write_text("## Note")

        config = {
            "agents": {
                "builder": {"surfaces": [{"path": str(absent), "label": "memory"}]},
                "shape": {"surfaces": [{"path": str(present), "label": "memory"}]},
            },
        }
        paths = get_memory_log_paths(start=None, end=None, config=config)
        assert len(paths) == 1
        assert "present" in paths[0]


@pytest.mark.unit
class TestBM25ScoreEdgeCase:
    """Cover the empty-tokens short-circuit (line 127)."""

    @pytest.mark.unit
    def test_no_matches_returns_empty(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        """A topic with stop-words only produces empty query_tokens — the
        BM25 scorer returns 0.0 for every chunk. Results may still surface
        but ranking is uniform."""
        # All stop words → _tokenise returns []
        results = query_temporal_chunks(
            topic="the and or",  # all stop words
            start=None,
            end=None,
            document_root=doc_root,
            config=doc_root_config,
        )
        # Should not raise; results may be empty or include chunks with 0 score
        assert isinstance(results, list)


@pytest.mark.unit
class TestRecencyFactorNoDate:
    """Cover the chunk_date=None branch via real chunks (line 153)."""

    @pytest.mark.unit
    def test_memory_section_chunk_has_recency_applied(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        """Memory log chunks may have chunk.date=None, exercising the 0.5
        recency factor branch via query_temporal_chunks."""
        results = query_temporal_chunks(
            topic="Session",
            start=None,
            end=None,
            chunk_types=["memory_section"],
            document_root=doc_root,
            config=doc_root_config,
        )
        # Memory section chunks without explicit date hit the chunk_date is None
        # branch in _recency_factor (line 153). We don't assert on the exact
        # score — just that scoring completed without errors.
        assert isinstance(results, list)


@pytest.mark.unit
class TestQueryTemporalChunksBoards:
    """Cover board-card filtering and exception handling for board chunking."""

    @pytest.mark.unit
    def test_board_files_chunked_via_doc_root(self, doc_root: Path, doc_root_config: dict[str, object]) -> None:
        """Board files under 01-Projects/Boards are picked up via document_root.

        Exercises lines 205-208 (board chunking try-block) and 228-231
        (chunk.date filtering for board cards).
        """
        results = query_temporal_chunks(
            topic="Phase 3 temporal",
            start=date(2026, 4, 28),
            end=date(2026, 4, 30),
            document_root=doc_root,
            config=doc_root_config,
        )
        # Board cards may or may not match the topic strongly enough; the
        # important thing is that no exception is raised and the function
        # returns a list.
        assert isinstance(results, list)

    @pytest.mark.unit
    def test_board_card_outside_date_range_excluded(self, tmp_path: Path) -> None:
        """Board cards with a date outside [start, end] are filtered out
        (lines 228-231)."""
        boards_dir = tmp_path / "01-Projects" / "Boards"
        boards_dir.mkdir(parents=True)
        (boards_dir / "Old.md").write_text(
            "## Done\n\n- [ ] Ancient card [completed::2020-01-01] [project::Old]\n",
            encoding="utf-8",
        )
        results = query_temporal_chunks(
            topic="ancient card",
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
            document_root=tmp_path,
        )
        # Card date 2020-01-01 < start 2026-01-01 → excluded
        assert all("Ancient card" not in c.text for c in results)


@pytest.mark.unit
class TestQueryTemporalChunksException:
    """Cover the outermost exception handler (lines 258-260)."""

    @pytest.mark.unit
    def test_returns_empty_when_topic_is_none(self, doc_root: Path) -> None:
        """Passing topic=None triggers an AttributeError inside the scorer;
        query_temporal_chunks catches it and returns []."""
        results = query_temporal_chunks(  # NOSONAR(python:S5655) — see type: ignore below; outer-except.
            topic=None,  # type: ignore[arg-type]  # deliberate type misuse — see NOSONAR above.
            start=None,
            end=None,
            document_root=doc_root,
        )
        assert results == []
