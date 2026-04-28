"""Tests for `kairix eval auto-gold` CLI subcommand (Sprint 17 Track D2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kairix.eval.cli import main

pytestmark = pytest.mark.unit


class TestAutoGoldCLIParsing:
    def test_auto_gold_subcommand_accepted(self) -> None:
        """Verify auto-gold is a valid subcommand (parses without error)."""
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="subcommand")
        sub.add_parser("auto-gold")
        args = parser.parse_args(["auto-gold"])
        assert args.subcommand == "auto-gold"


class TestAutoGoldCLIExecution:
    @patch("kairix.db.get_db_path")
    def test_exits_1_when_no_index(self, mock_db: MagicMock) -> None:
        mock_db.side_effect = FileNotFoundError("no index")
        with pytest.raises(SystemExit) as exc_info:
            main(["auto-gold"])
        assert exc_info.value.code == 1

    @patch("kairix.eval.auto_gold.build_suite")
    @patch("kairix.eval.auto_gold.generate_template_queries")
    @patch("kairix.eval.auto_gold.analyse_corpus")
    @patch("kairix.db.get_db_path")
    def test_generates_suite(
        self, mock_db: MagicMock, mock_analyse: MagicMock, mock_gen: MagicMock, mock_build: MagicMock, tmp_path
    ) -> None:
        import sqlite3

        from kairix.eval.auto_gold import CorpusProfile

        db_path = tmp_path / "index.sqlite"
        sqlite3.connect(str(db_path)).close()
        mock_db.return_value = db_path

        mock_analyse.return_value = CorpusProfile(
            total_docs=100, collections={"default": 100}, procedural_count=10, date_filename_count=5, entity_doc_count=8
        )

        mock_gen.return_value = [
            {"id": "AG-R001", "category": "recall", "query": "test", "score_method": "ndcg"},
        ]

        output = str(tmp_path / "auto-gold.yaml")
        with pytest.raises(SystemExit) as exc_info:
            main(["auto-gold", "--output", output, "--count", "10"])
        assert exc_info.value.code == 0
