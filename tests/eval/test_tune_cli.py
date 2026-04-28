"""Tests for `kairix eval tune` CLI subcommand (Sprint 17 Track D3)."""

from __future__ import annotations

import json

import pytest

from kairix.eval.cli import main

pytestmark = pytest.mark.unit


class TestTuneCLIParsing:
    def test_tune_requires_result(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["tune"])
        assert exc_info.value.code == 2  # argparse exits 2 for missing required


class TestTuneCLIExecution:
    def test_exits_1_for_missing_file(self, tmp_path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["tune", "--result", str(tmp_path / "nonexistent.json")])
        assert exc_info.value.code == 1

    def test_exits_1_for_invalid_json(self, tmp_path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        with pytest.raises(SystemExit) as exc_info:
            main(["tune", "--result", str(bad_file)])
        assert exc_info.value.code == 1

    def test_exits_0_all_above_floor(self, tmp_path) -> None:
        result_file = tmp_path / "result.json"
        result_file.write_text(
            json.dumps(
                {
                    "summary": {
                        "category_scores": {
                            "recall": 0.80,
                            "temporal": 0.60,
                            "entity": 0.75,
                            "conceptual": 0.70,
                            "multi_hop": 0.55,
                            "procedural": 0.65,
                        }
                    }
                }
            )
        )
        with pytest.raises(SystemExit) as exc_info:
            main(["tune", "--result", str(result_file)])
        assert exc_info.value.code == 0

    def test_exits_0_with_recommendations(self, tmp_path) -> None:
        result_file = tmp_path / "result.json"
        result_file.write_text(
            json.dumps(
                {
                    "summary": {
                        "category_scores": {
                            "recall": 0.30,  # below floor
                            "temporal": 0.20,  # below floor
                            "entity": 0.80,
                            "conceptual": 0.10,  # below floor
                        }
                    }
                }
            )
        )
        with pytest.raises(SystemExit) as exc_info:
            main(["tune", "--result", str(result_file), "--floor", "0.50"])
        assert exc_info.value.code == 0

    def test_custom_floor(self, tmp_path) -> None:
        result_file = tmp_path / "result.json"
        result_file.write_text(
            json.dumps(
                {
                    "summary": {
                        "category_scores": {
                            "recall": 0.70,
                        }
                    }
                }
            )
        )
        # With floor=0.80, recall is weak
        with pytest.raises(SystemExit) as exc_info:
            main(["tune", "--result", str(result_file), "--floor", "0.80"])
        assert exc_info.value.code == 0
