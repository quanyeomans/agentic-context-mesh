"""Layered ``kairix.config.yaml`` resolution + merge core (#492).

Lowest-level config module — imported by BOTH :mod:`kairix.paths` and
:mod:`kairix.core.search.config_loader` so every runtime reader of the
operator config resolves the same ``(base, overlay)`` file pair and sees
the same deep-merged mapping the setup wizard writes. Extracted from
``config_loader`` because a top-level ``paths → config_loader`` import
would cycle (``config_loader`` imports ``kairix.paths``); this module
therefore imports nothing from either of them.

Resolution matrix (see :func:`resolve_layered_paths`):

1. ``KAIRIX_CONFIG_OVERLAY_PATH`` / ``KAIRIX_CONFIG_BASE_PATH`` set →
   layered mode: image-bundled base + sparse operator overlay.
2. ``KAIRIX_CONFIG_PATH`` set → legacy single-file mode.
3. ``./kairix.config.yaml`` exists → cwd discovery (legacy fallback).
4. ``$XDG_CONFIG_HOME/kairix/kairix.config.yaml`` (fallback
   ``~/.config/kairix/...``) exists → pip-install default, the same
   location ``kairix init`` writes (``paths.config_dir(Mode.user)``).
5. Nothing found → callers fall back to their built-in defaults.

Env access stays F2/F4-clean: every function takes an explicit ``env``
mapping seam; only when callers pass ``None`` is the live process
environment snapshotted.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILENAME = "kairix.config.yaml"

# The path the Docker image bundles its canonical config at. v2026.6.8+
# unified container places this at /etc/kairix/kairix.config.yaml per
# the FHS layout (the Dockerfile's COPY target + KAIRIX_CONFIG_PATH env).
# Operators overlay sparse host-side overrides via
# ``KAIRIX_CONFIG_OVERLAY_PATH``; the layered loader reads BASE from
# this location unless ``KAIRIX_CONFIG_BASE_PATH`` is set to point
# elsewhere. Pre-v2026.6.8 images placed it at /opt/kairix/kairix.config.yaml
# — operators on legacy images set KAIRIX_CONFIG_BASE_PATH explicitly.
DEFAULT_IMAGE_BASE_PATH = Path("/etc/kairix/kairix.config.yaml")

_ENV_OVERLAY_PATH = "KAIRIX_CONFIG_OVERLAY_PATH"
_ENV_BASE_PATH = "KAIRIX_CONFIG_BASE_PATH"
_ENV_LEGACY_PATH = "KAIRIX_CONFIG_PATH"
_ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"


def _live_env() -> dict[str, str]:
    """Snapshot the process environment — single os.environ read site."""
    import os

    return dict(os.environ)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` ON TOP OF ``base``; returns a new dict.

    Semantics:
      - dict + dict  → recursive merge (operator's nested key wins at the
        leaf; siblings at every level survive from base)
      - list + list  → overlay REPLACES base (operator declaring their own
        ``collections.shared`` gets exactly their list, not a concat)
      - scalar / type-mismatch → overlay wins

    Neither input is mutated; callers can safely reuse both.
    """
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        # Top-level call should always pass dicts; defensive return supports
        # recursive descent into mixed-type values.
        return overlay
    result: dict[str, Any] = {}
    for key in {*base.keys(), *overlay.keys()}:
        if key in overlay:
            if key in base and isinstance(base[key], dict) and isinstance(overlay[key], dict):
                result[key] = deep_merge(base[key], overlay[key])
            else:
                result[key] = overlay[key]
        else:
            result[key] = base[key]
    return result


def user_config_path(*, env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """The pip-install default config location — where ``kairix init`` writes.

    ``$XDG_CONFIG_HOME/kairix/kairix.config.yaml`` with the standard
    ``~/.config`` fallback — mirrors ``paths.config_dir(Mode.user)`` and
    the secrets bundle resolution in ``kairix.secrets.store``. The
    ``env`` / ``home`` kwargs are the F2-clean test seams.
    """
    e = env if env is not None else _live_env()
    xdg = e.get(_ENV_XDG_CONFIG_HOME, "").strip()
    if xdg:
        config_base = Path(xdg).expanduser()
    else:
        home_dir = home if home is not None else Path.home()
        config_base = home_dir / ".config"
    return config_base / "kairix" / DEFAULT_CONFIG_FILENAME


def _user_config_candidate(env: dict[str, str]) -> Path | None:
    """Read-side probe for :func:`user_config_path` — env-derived only.

    Derives the home directory from the ``env`` mapping (``HOME``)
    rather than ``Path.home()`` so tests driving the resolver with an
    explicit env dict stay hermetic: ``env={}`` probes nothing.
    """
    xdg = env.get(_ENV_XDG_CONFIG_HOME, "").strip()
    if xdg:
        return user_config_path(env=env)
    home = env.get("HOME", "").strip()
    if home:
        return user_config_path(env=env, home=Path(home))
    return None


def _resolve_layered_base(base_value: str, image_base_default: Path) -> Path | None:
    """Resolve the base path for layered mode.

    Explicit ``KAIRIX_CONFIG_BASE_PATH`` wins; otherwise the image-bundled
    default applies when it exists. Missing files log a warning and yield
    ``None`` so the caller can degrade gracefully.
    """
    if base_value:
        base_p = Path(base_value).expanduser()
        if base_p.is_file():
            return base_p
        logger.warning("config_layers: KAIRIX_CONFIG_BASE_PATH=%r not found", base_value)
        return None
    return image_base_default if image_base_default.is_file() else None


def _resolve_layered_overlay(overlay_value: str) -> Path | None:
    """Resolve the overlay path for layered mode.

    Empty string → ``None`` (base-only layered mode). Missing file logs a
    warning and yields ``None`` so the caller still loads the base alone.
    """
    if not overlay_value:
        return None
    overlay_p = Path(overlay_value).expanduser()
    if overlay_p.is_file():
        return overlay_p
    logger.warning(
        "config_layers: KAIRIX_CONFIG_OVERLAY_PATH=%r not found — loading base alone",
        overlay_value,
    )
    return None


def _resolve_legacy_or_cwd(env: dict[str, str]) -> tuple[Path | None, Path | None]:
    """Resolve legacy single-file mode, cwd discovery, or the XDG default."""
    legacy_value = env.get(_ENV_LEGACY_PATH, "").strip()
    if legacy_value:
        legacy_p = Path(legacy_value).expanduser()
        if legacy_p.is_file():
            return legacy_p, None
        logger.warning("config_layers: KAIRIX_CONFIG_PATH=%r not found — using defaults", legacy_value)
        return None, None

    cwd_p = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_p.is_file():
        return cwd_p, None

    user_p = _user_config_candidate(env)
    if user_p is not None and user_p.is_file():
        return user_p, None
    return None, None


def resolve_layered_paths(
    *,
    env: dict[str, str] | None = None,
    image_base_default: Path = DEFAULT_IMAGE_BASE_PATH,
) -> tuple[Path | None, Path | None]:
    """Return ``(base_path, overlay_path)`` — F2-clean env resolution.

    Resolution matrix:
      - ``KAIRIX_CONFIG_OVERLAY_PATH`` set → layered mode:
          base ← ``KAIRIX_CONFIG_BASE_PATH`` or ``image_base_default``,
          overlay ← env var.
      - ``KAIRIX_CONFIG_PATH`` set (and overlay not set) → legacy
        single-file mode: ``(single_path, None)``.
      - ``./kairix.config.yaml`` exists → cwd-discovery: ``(cwd_path, None)``.
      - ``$XDG_CONFIG_HOME/kairix/kairix.config.yaml`` exists → the
        pip-install default ``kairix init`` writes: ``(user_path, None)``.
      - Otherwise → ``(None, None)`` — caller falls back to defaults.

    The ``env`` kwarg makes this F2-clean: tests pass an explicit dict
    instead of mutating ``os.environ`` via monkeypatch.setenv.
    """
    if env is None:
        env = _live_env()

    overlay_value = env.get(_ENV_OVERLAY_PATH, "").strip()
    base_value = env.get(_ENV_BASE_PATH, "").strip()

    if overlay_value or base_value:
        return _resolve_layered_base(base_value, image_base_default), _resolve_layered_overlay(overlay_value)

    return _resolve_legacy_or_cwd(env)


def load_yaml_mapping(path: Path | None) -> dict[str, Any]:
    """Load YAML file → dict; empty dict on missing path or parse failure."""
    if path is None:
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover — PyYAML is a hard dep in pyproject; only fires in stripped builds
        logger.warning("config_layers: PyYAML not installed — empty config")
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("config_layers: failed to read %s — %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("config_layers: %s root is not a mapping — empty config", path)
        return {}
    return data


def load_merged_mapping(
    *,
    env: dict[str, str] | None = None,
    image_base_default: Path = DEFAULT_IMAGE_BASE_PATH,
) -> dict[str, Any]:
    """Resolve + read + deep-merge the operator config into one mapping.

    This is the read path every runtime consumer of wizard-written
    config flows through (#492): the worker's ``topology_v2`` boot
    apply, ``paths.load_top_level_config`` (document root and friends),
    and the feature-flag config overlay. Returns ``{}`` when no config
    file resolves — the truthful fresh-install answer.

    Schema-compat validation is NOT enforced here; readers of this
    mapping degrade gracefully by contract. The retrieval loader
    (``config_loader.load_layered_yaml``) keeps the loud
    ``validate_schema_compat`` gate.
    """
    base_path, overlay_path = resolve_layered_paths(env=env, image_base_default=image_base_default)
    base_data = load_yaml_mapping(base_path)
    overlay_data = load_yaml_mapping(overlay_path) if overlay_path is not None else {}
    if overlay_data:
        return deep_merge(base_data, overlay_data)
    return base_data
