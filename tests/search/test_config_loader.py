"""Tests for kairix YAML config loader."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from kairix.search.config import EntityBoostConfig, ProceduralBoostConfig, RerankConfig, RetrievalConfig, TemporalBoostConfig
from kairix.search.config_loader import ConfigValidationError, _parse_config, _validate_config, load_config, _resolve_config_path


class TestParseConfig:
    def test_empty_dict_returns_defaults(self):
        cfg = _parse_config({})
        defaults = RetrievalConfig.defaults()
        assert cfg.entity.enabled == defaults.entity.enabled
        assert cfg.procedural.factor == defaults.procedural.factor

    def test_entity_enabled_false(self):
        cfg = _parse_config({"retrieval": {"boosts": {"entity": {"enabled": False}}}})
        assert cfg.entity.enabled is False

    def test_procedural_custom_factor(self):
        cfg = _parse_config({"retrieval": {"boosts": {"procedural": {"factor": 1.8}}}})
        assert cfg.procedural.factor == pytest.approx(1.8)

    def test_custom_path_patterns(self):
        cfg = _parse_config({
            "retrieval": {"boosts": {"procedural": {"path_patterns": [r"(?:^|/)docs/"]}}}
        })
        assert r"(?:^|/)docs/" in cfg.procedural.path_patterns

    def test_temporal_date_path_boost_enabled(self):
        cfg = _parse_config({
            "retrieval": {"boosts": {"temporal": {"date_path_boost": {"enabled": True, "factor": 1.5}}}}
        })
        assert cfg.temporal.date_path_boost_enabled is True
        assert cfg.temporal.date_path_boost_factor == pytest.approx(1.5)

    def test_temporal_chunk_date_boost_enabled(self):
        cfg = _parse_config({
            "retrieval": {"boosts": {"temporal": {"chunk_date_boost": {"enabled": True, "decay_halflife_days": 14}}}}
        })
        assert cfg.temporal.chunk_date_boost_enabled is True
        assert cfg.temporal.chunk_date_decay_halflife_days == 14

    def test_temporal_chunk_date_guard_explicit_only_defaults_true(self):
        cfg = _parse_config({})
        assert cfg.temporal.chunk_date_boost_guard_explicit_only is True

    def test_temporal_chunk_date_guard_explicit_only_can_disable(self):
        cfg = _parse_config({
            "retrieval": {"boosts": {"temporal": {"chunk_date_boost": {"guard_explicit_only": False}}}}
        })
        assert cfg.temporal.chunk_date_boost_guard_explicit_only is False

    def test_rerank_config_parsed(self):
        cfg = _parse_config({"retrieval": {"rerank": {"enabled": True, "candidate_limit": 30}}})
        assert cfg.rerank.enabled is True
        assert cfg.rerank.candidate_limit == 30

    def test_rerank_defaults_disabled(self):
        cfg = _parse_config({})
        assert cfg.rerank.enabled is False


class TestValidateConfig:
    def test_valid_defaults_pass(self):
        cfg = _parse_config({})
        _validate_config(cfg)  # should not raise

    def test_entity_factor_out_of_range_raises(self):
        cfg = _parse_config({"retrieval": {"boosts": {"entity": {"factor": 99.0}}}})
        with pytest.raises(ConfigValidationError, match="entity.factor"):
            _validate_config(cfg)

    def test_entity_cap_below_min_raises(self):
        cfg = _parse_config({"retrieval": {"boosts": {"entity": {"cap": 0.5}}}})
        with pytest.raises(ConfigValidationError, match="entity.cap"):
            _validate_config(cfg)

    def test_procedural_factor_out_of_range_raises(self):
        cfg = _parse_config({"retrieval": {"boosts": {"procedural": {"factor": 0.5}}}})
        with pytest.raises(ConfigValidationError, match="procedural.factor"):
            _validate_config(cfg)

    def test_multiple_errors_reported_together(self):
        cfg = _parse_config({
            "retrieval": {"boosts": {
                "entity": {"factor": 99.0, "cap": 0.1},
            }}
        })
        with pytest.raises(ConfigValidationError) as exc_info:
            _validate_config(cfg)
        msg = str(exc_info.value)
        assert "entity.factor" in msg
        assert "entity.cap" in msg

    def test_invalid_config_not_silently_swallowed(self, tmp_path, monkeypatch):
        """ConfigValidationError must propagate — never fall back to defaults on invalid config."""
        yaml = pytest.importorskip("yaml")
        config_file = tmp_path / "kairix.config.yaml"
        config_file.write_text(textwrap.dedent("""
            retrieval:
              boosts:
                entity:
                  factor: 999.0
        """))
        monkeypatch.setenv("KAIRIX_CONFIG_PATH", str(config_file))
        from kairix.search import config_loader
        config_loader._load_cached.cache_clear()
        with pytest.raises(ConfigValidationError):
            load_config()


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KAIRIX_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        # Clear lru_cache so path is re-resolved
        from kairix.search import config_loader
        config_loader._load_cached.cache_clear()
        cfg = load_config()
        assert isinstance(cfg, RetrievalConfig)

    def test_loads_from_env_var(self, tmp_path, monkeypatch):
        yaml = pytest.importorskip("yaml")
        config_file = tmp_path / "my-kairix.yaml"
        config_file.write_text(textwrap.dedent("""
            retrieval:
              boosts:
                entity:
                  enabled: false
        """))
        monkeypatch.setenv("KAIRIX_CONFIG_PATH", str(config_file))
        from kairix.search import config_loader
        config_loader._load_cached.cache_clear()
        cfg = load_config()
        assert cfg.entity.enabled is False
