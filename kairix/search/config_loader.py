"""
YAML configuration loader for kairix retrieval config.

Resolution order:
  1. KAIRIX_CONFIG_PATH env var → explicit path
  2. ./kairix.config.yaml → current working directory
  3. Built-in defaults → no file required

Missing file silently falls back to defaults.
YAML parse failure logs a warning and falls back to defaults.
Result is cached per process (lru_cache on resolved path).
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from kairix.search.config import (
    EntityBoostConfig,
    ProceduralBoostConfig,
    RetrievalConfig,
    TemporalBoostConfig,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILENAME = "kairix.config.yaml"


def _resolve_config_path() -> Path | None:
    """Find the config file path from env var or working directory."""
    env_path = os.environ.get("KAIRIX_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        logger.warning("config_loader: KAIRIX_CONFIG_PATH=%r not found — using defaults", env_path)
        return None
    cwd_path = Path.cwd() / _DEFAULT_CONFIG_FILENAME
    if cwd_path.is_file():
        return cwd_path
    return None


@lru_cache(maxsize=1)
def _load_cached(config_path: Path | None) -> RetrievalConfig:
    """Load and cache RetrievalConfig from path. Returns defaults if path is None."""
    if config_path is None:
        return RetrievalConfig.defaults()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("config_loader: PyYAML not installed — using defaults")
        return RetrievalConfig.defaults()

    try:
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("config_loader: failed to read %s — %s — using defaults", config_path, e)
        return RetrievalConfig.defaults()

    return _parse_config(data)


def load_config() -> RetrievalConfig:
    """
    Load RetrievalConfig from YAML file or return defaults.

    Call this once at startup. Result is cached per process.
    """
    path = _resolve_config_path()
    if path is not None:
        logger.info("config_loader: loading config from %s", path)
    return _load_cached(path)


def _parse_config(data: dict) -> RetrievalConfig:
    """Parse YAML dict into RetrievalConfig. Returns defaults for any missing/invalid section."""
    retrieval = data.get("retrieval", {}) or {}
    boosts = retrieval.get("boosts", {}) or {}

    entity_cfg = _parse_entity(boosts.get("entity", {}) or {})
    procedural_cfg = _parse_procedural(boosts.get("procedural", {}) or {})
    temporal_cfg = _parse_temporal(boosts.get("temporal", {}) or {})

    return RetrievalConfig(
        entity=entity_cfg,
        procedural=procedural_cfg,
        temporal=temporal_cfg,
    )


def _parse_entity(d: dict) -> EntityBoostConfig:
    defaults = EntityBoostConfig()
    return EntityBoostConfig(
        enabled=bool(d.get("enabled", defaults.enabled)),
        factor=float(d.get("factor", defaults.factor)),
        cap=float(d.get("cap", defaults.cap)),
    )


def _parse_procedural(d: dict) -> ProceduralBoostConfig:
    defaults = ProceduralBoostConfig()
    patterns = d.get("path_patterns")
    return ProceduralBoostConfig(
        enabled=bool(d.get("enabled", defaults.enabled)),
        factor=float(d.get("factor", defaults.factor)),
        path_patterns=tuple(patterns) if patterns else defaults.path_patterns,
    )


def _parse_temporal(d: dict) -> TemporalBoostConfig:
    defaults = TemporalBoostConfig()
    date_path = d.get("date_path_boost", {}) or {}
    chunk_date = d.get("chunk_date_boost", {}) or {}
    return TemporalBoostConfig(
        date_path_boost_enabled=bool(date_path.get("enabled", defaults.date_path_boost_enabled)),
        date_path_boost_factor=float(date_path.get("factor", defaults.date_path_boost_factor)),
        date_path_recency_window_days=int(date_path.get("recency_window_days", defaults.date_path_recency_window_days)),
        chunk_date_boost_enabled=bool(chunk_date.get("enabled", defaults.chunk_date_boost_enabled)),
        chunk_date_decay_halflife_days=int(
            chunk_date.get("decay_halflife_days", defaults.chunk_date_decay_halflife_days)
        ),
    )
