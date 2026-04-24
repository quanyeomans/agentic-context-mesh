"""
YAML configuration loader for kairix retrieval config.

Resolution order:
  1. KAIRIX_CONFIG_PATH env var → explicit path
  2. ./kairix.config.yaml → current working directory
  3. Built-in defaults → no file required

Missing file silently falls back to defaults.
YAML parse failure logs a warning and falls back to defaults.
Invalid config values raise ConfigValidationError — do NOT fall back silently,
as silent fallback can mask misconfiguration in production deployments.
Result is cached per process (lru_cache on resolved path).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from kairix.search.config import (
    EntityBoostConfig,
    ProceduralBoostConfig,
    RerankConfig,
    RetrievalConfig,
    TemporalBoostConfig,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILENAME = "kairix.config.yaml"


class ConfigValidationError(ValueError):
    """Raised at startup when kairix.config.yaml contains out-of-range values.

    Unlike YAML parse errors (which fall back to defaults), validation errors
    are propagated to the caller — an invalid config should not silently produce
    unexpected retrieval behaviour in production.
    """


# Valid ranges for numeric config fields. Tuple is (min_inclusive, max_inclusive).
_VALID_RANGES: dict[str, tuple[float, float]] = {
    "entity.factor": (0.0, 10.0),
    "entity.cap": (1.0, 10.0),
    "procedural.factor": (1.0, 5.0),
    "temporal.date_path_boost_factor": (1.0, 5.0),
    "temporal.date_path_recency_window_days": (1.0, 3650.0),
    "temporal.chunk_date_decay_halflife_days": (1.0, 3650.0),
    "rerank.candidate_limit": (1.0, 100.0),
}


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


def _validate_config(cfg: RetrievalConfig) -> None:
    """Raise ConfigValidationError if any field is outside its valid range.

    Called after parsing, before caching. Does NOT fall back to defaults —
    invalid configuration should surface as an error so operators notice it.
    """
    checks = {
        "entity.factor": cfg.entity.factor,
        "entity.cap": cfg.entity.cap,
        "procedural.factor": cfg.procedural.factor,
        "temporal.date_path_boost_factor": cfg.temporal.date_path_boost_factor,
        "temporal.date_path_recency_window_days": float(cfg.temporal.date_path_recency_window_days),
        "temporal.chunk_date_decay_halflife_days": float(cfg.temporal.chunk_date_decay_halflife_days),
        "rerank.candidate_limit": float(cfg.rerank.candidate_limit),
    }
    errors: list[str] = []
    for field_name, value in checks.items():
        lo, hi = _VALID_RANGES[field_name]
        if not (lo <= value <= hi):
            errors.append(f"  {field_name}: {value} is outside valid range [{lo}, {hi}]")

    if errors:
        raise ConfigValidationError(
            "kairix.config.yaml contains invalid values:\n" + "\n".join(errors)
        )


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

    try:
        cfg = _parse_config(data)
        _validate_config(cfg)
        return cfg
    except ConfigValidationError:
        raise  # propagate — never fall back silently on invalid config
    except Exception as e:
        logger.warning("config_loader: failed to parse %s — %s — using defaults", config_path, e)
        return RetrievalConfig.defaults()


def load_config() -> RetrievalConfig:
    """
    Load RetrievalConfig from YAML file or return defaults.

    Call this once at startup. Result is cached per process.

    Raises:
        ConfigValidationError: if the config file contains out-of-range values.
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
    rerank_cfg = _parse_rerank((retrieval.get("rerank", {}) or {}))

    # Fusion strategy + RRF k
    defaults = RetrievalConfig.defaults()
    fusion = str(retrieval.get("fusion_strategy", defaults.fusion_strategy))
    if fusion not in ("bm25_primary", "rrf"):
        logger.warning("config_loader: unknown fusion_strategy %r — using bm25_primary", fusion)
        fusion = "bm25_primary"
    rrf_k = int(retrieval.get("rrf_k", defaults.rrf_k))

    return RetrievalConfig(
        fusion_strategy=fusion,
        rrf_k=rrf_k,
        entity=entity_cfg,
        procedural=procedural_cfg,
        temporal=temporal_cfg,
        rerank=rerank_cfg,
    )


# ---------------------------------------------------------------------------
# Collections parsing
# ---------------------------------------------------------------------------


@dataclass
class CollectionDef:
    """A configured document collection for search scoping."""

    name: str
    path: str  # relative to vault_root
    glob: str = "**/*.md"


@dataclass
class CollectionsConfig:
    """Parsed collections configuration."""

    shared: list[CollectionDef]
    agent_pattern: str = "{agent}-memory"
    agent_paths: dict[str, str] = field(default_factory=dict)


def parse_collections(data: dict) -> CollectionsConfig | None:
    """Parse the collections: section from config. Returns None if not present."""
    collections = data.get("collections")
    if not collections:
        return None

    shared_raw = collections.get("shared", [])
    shared = []
    for item in shared_raw:
        if isinstance(item, dict) and "name" in item:
            shared.append(CollectionDef(
                name=item["name"],
                path=item.get("path", "."),
                glob=item.get("glob", "**/*.md"),
            ))

    return CollectionsConfig(
        shared=shared,
        agent_pattern=collections.get("agent_pattern", "{agent}-memory"),
        agent_paths=collections.get("agent_paths", {}),
    )


def load_collections() -> CollectionsConfig | None:
    """Load collections config from YAML. Returns None if not configured."""
    path = _resolve_config_path()
    if path is None:
        return None
    try:
        import yaml
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return parse_collections(data)
    except Exception:
        return None


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
        chunk_date_boost_guard_explicit_only=bool(
            chunk_date.get("guard_explicit_only", defaults.chunk_date_boost_guard_explicit_only)
        ),
    )


def _parse_rerank(d: dict) -> RerankConfig:
    defaults = RerankConfig()
    return RerankConfig(
        enabled=bool(d.get("enabled", defaults.enabled)),
        model=str(d.get("model", defaults.model)),
        candidate_limit=int(d.get("candidate_limit", defaults.candidate_limit)),
    )
