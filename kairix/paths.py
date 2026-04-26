"""Centralised path resolution for kairix.

Every module that needs a file path imports from here instead of
hardcoding defaults. Paths are resolved once and cached.

Resolution order (highest wins):
  1. Environment variables (KAIRIX_DOCUMENT_ROOT, KAIRIX_DB_PATH, etc.)
     - KAIRIX_VAULT_ROOT is accepted as a deprecated fallback
  2. Config file paths: section (kairix.config.yaml)
  3. Platform-aware defaults (macOS, Linux, Docker)
"""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _is_docker() -> bool:
    """Detect if running inside a Docker container."""
    return (
        os.path.exists("/.dockerenv")
        or os.environ.get("KAIRIX_DOCKER", "") == "1"
        or os.environ.get("container", "") != ""
    )


def _is_service_install() -> bool:
    """Detect if kairix was installed as a system service (/opt/kairix)."""
    return Path("/opt/kairix/.venv").exists()


def _default_document_root() -> Path:
    """Platform-appropriate default document store location.

    Docker: /data/vault (bind mount from host)
    Server: /var/lib/kairix/documents (admin configures)
    User (all platforms): ~/Documents (most common document location)
    """
    if _is_docker():
        return Path("/data/vault")
    if _is_service_install():
        return Path("/var/lib/kairix/documents")
    return Path.home() / "Documents"


def _default_vault_root() -> Path:
    """Deprecated alias for _default_document_root."""
    return _default_document_root()


def _default_data_dir() -> Path:
    """Platform-appropriate data directory for DB, vectors, and state.

    Docker: /data/kairix
    Server: /var/lib/kairix
    Linux/macOS user: ~/.local/share/kairix (XDG_DATA_HOME)
    Windows user: %LOCALAPPDATA%/kairix
    """
    if _is_docker():
        return Path("/data/kairix")
    if _is_service_install():
        return Path("/var/lib/kairix")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "kairix"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "kairix"
    return Path.home() / ".local" / "share" / "kairix"


def _default_cache_dir() -> Path:
    """Platform-appropriate cache directory for temporary data.

    Docker: /data/kairix (same as data dir)
    Server: /var/cache/kairix
    Linux/macOS user: ~/.cache/kairix (XDG_CACHE_HOME)
    Windows user: %LOCALAPPDATA%/kairix/cache
    """
    if _is_docker():
        return Path("/data/kairix")
    if _is_service_install():
        return Path("/var/cache/kairix")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "kairix" / "cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "kairix"
    return Path.home() / ".cache" / "kairix"


def _default_workspace_root() -> Path:
    """Platform-appropriate workspace root for agent memory logs."""
    if _is_docker():
        return Path("/data/workspaces")
    if _is_service_install():
        return Path("/data/workspaces")
    return Path.home() / ".kairix" / "workspaces"


@dataclass(frozen=True)
class KairixPaths:
    """Resolved paths for a kairix deployment.

    Use KairixPaths.resolve() to get paths based on your environment.
    All paths are absolute.
    """

    vault_root: Path
    db_path: Path
    log_dir: Path
    workspace_root: Path

    @classmethod
    def resolve(cls) -> KairixPaths:
        """Resolve paths from environment variables, config file, or platform defaults.

        Call this once at startup. The result is cached per process.
        """
        return _resolve_cached()


@lru_cache(maxsize=1)
def _resolve_cached() -> KairixPaths:
    """Internal cached resolution — called by KairixPaths.resolve()."""
    cache_dir = _default_cache_dir()

    # Try loading paths from config file
    config_paths = _load_paths_from_config()

    env_doc_root = os.environ.get("KAIRIX_DOCUMENT_ROOT")
    env_vault_root = os.environ.get("KAIRIX_VAULT_ROOT")
    if env_vault_root and not env_doc_root:
        warnings.warn(
            "KAIRIX_VAULT_ROOT is deprecated, use KAIRIX_DOCUMENT_ROOT instead",
            DeprecationWarning,
            stacklevel=3,
        )
    vault_root = Path(
        env_doc_root
        or env_vault_root
        or config_paths.get("vault_root")
        or str(_default_document_root())
    ).expanduser()

    db_path = Path(
        os.environ.get("KAIRIX_DB_PATH")
        or config_paths.get("db_path")
        or str(cache_dir / "index.sqlite")
    ).expanduser()

    log_dir = Path(
        os.environ.get("KAIRIX_LOG_DIR")
        or os.environ.get("LOG_DIR")
        or config_paths.get("log_dir")
        or str(cache_dir / "logs")
    ).expanduser()

    workspace_root = Path(
        os.environ.get("KAIRIX_WORKSPACE_ROOT")
        or config_paths.get("workspace_root")
        or str(_default_workspace_root())
    ).expanduser()

    return KairixPaths(
        vault_root=vault_root,
        db_path=db_path,
        log_dir=log_dir,
        workspace_root=workspace_root,
    )


def _load_paths_from_config() -> dict[str, str]:
    """Load the paths: section from kairix.config.yaml if it exists."""
    config_path = os.environ.get("KAIRIX_CONFIG_PATH", "kairix.config.yaml")
    try:
        import yaml  # type: ignore[import-untyped]

        p = Path(config_path).expanduser()
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f) or {}
            result: dict[str, str] = data.get("paths", {})
            return result
    except Exception:
        pass
    return {}


def clear_cache() -> None:
    """Clear the cached path resolution. Call after changing env vars in tests."""
    _resolve_cached.cache_clear()


# Convenience functions — import these directly instead of calling KairixPaths.resolve()

def document_root() -> Path:
    """Return the document store root path (was: vault_root)."""
    return KairixPaths.resolve().vault_root


def vault_root() -> Path:
    """Deprecated alias for document_root()."""
    return document_root()


def db_path() -> Path:
    """Get the database path."""
    return KairixPaths.resolve().db_path


def log_dir() -> Path:
    """Get the log directory path."""
    return KairixPaths.resolve().log_dir


def workspace_root() -> Path:
    """Get the workspace root path."""
    return KairixPaths.resolve().workspace_root
