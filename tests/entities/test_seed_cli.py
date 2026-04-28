"""Tests for `kairix entity seed` CLI subcommand (Sprint 17 Track D1)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kairix.entities.cli import build_parser, main

pytestmark = pytest.mark.unit


class TestSeedCLIParsing:
    def test_seed_subcommand_exists(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["seed"])
        assert args.command == "seed"

    def test_seed_dry_run_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["seed", "--dry-run"])
        assert args.dry_run is True

    def test_seed_limit_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["seed", "--limit", "100"])
        assert args.limit == 100


class TestSeedCLIExecution:
    @patch("kairix.db.get_db_path")
    def test_exits_1_when_no_index(self, mock_db: MagicMock) -> None:
        mock_db.side_effect = FileNotFoundError("no index")
        result = main(["seed"])
        assert result == 1

    @patch("kairix.entities.seed.scan_for_entities")
    @patch("kairix.db.get_db_path")
    def test_dry_run_does_not_seed(self, mock_db: MagicMock, mock_scan: MagicMock, tmp_path) -> None:
        from kairix.entities.seed import EntityCandidate

        mock_db.return_value = tmp_path / "index.sqlite"
        # Create a minimal sqlite DB
        import sqlite3

        db = sqlite3.connect(str(mock_db.return_value))
        db.execute("CREATE TABLE documents (id INTEGER, path TEXT, title TEXT, active INTEGER)")
        db.close()

        mock_scan.return_value = [
            EntityCandidate(name="Test Corp", entity_type="Organisation", confidence=0.9),
        ]

        result = main(["seed", "--dry-run"])
        assert result == 0

    @patch("kairix.entities.seed.scan_for_entities")
    @patch("kairix.db.get_db_path")
    def test_returns_0_when_no_candidates(self, mock_db: MagicMock, mock_scan: MagicMock, tmp_path) -> None:
        mock_db.return_value = tmp_path / "index.sqlite"
        import sqlite3

        db = sqlite3.connect(str(mock_db.return_value))
        db.execute("CREATE TABLE documents (id INTEGER, path TEXT, title TEXT, active INTEGER)")
        db.close()

        mock_scan.return_value = []
        result = main(["seed"])
        assert result == 0
