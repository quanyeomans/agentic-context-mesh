"""
Kairix storage layer — owns the SQLite database, FTS5 index, and sqlite-vec vectors.

Kairix maintains its own
database at ``~/.cache/kairix/index.sqlite`` (configurable via ``KAIRIX_DB_PATH``).

Public API:
  - get_db_path()       — resolve the database file path
  - open_db()           — open a connection with sqlite-vec loaded
  - load_extensions()   — load sqlite-vec into an existing connection
"""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Environment variable for explicit DB path override
_DB_PATH_ENV = "KAIRIX_DB_PATH"

# sqlite-vec env override (preserved for deployments with custom vec0.so location)
_SQLITE_VEC_ENV = "SQLITE_VEC_PATH"

# Embedding dimensions — configurable via KAIRIX_EMBED_DIMS
EMBED_VECTOR_DIMS = int(os.environ.get("KAIRIX_EMBED_DIMS", "1536"))


def get_db_path() -> Path:
    """
    Resolve the kairix database path.

    Search order:
      1. ``KAIRIX_DB_PATH`` environment variable (explicit override)
      2. ``~/.cache/kairix/index.sqlite`` (default kairix location)

    Returns the path (which may not exist yet for fresh installs).
    """
    # 1. Explicit override
    env_path = os.environ.get(_DB_PATH_ENV)
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        # If explicitly set but doesn't exist, return it anyway — caller
        # will create it (e.g. kairix scan on first run).
        return p

    # 2. Default kairix location
    kairix_db = Path.home() / ".cache" / "kairix" / "index.sqlite"
    if kairix_db.exists():
        return kairix_db

    # No DB exists — return the default path for creation
    return kairix_db


def _find_sqlite_vec() -> str | None:
    """
    Locate the sqlite-vec extension.

    Search order:
      1. ``SQLITE_VEC_PATH`` environment variable
      2. PyPI ``sqlite-vec`` package (``import sqlite_vec``)
      3. System paths (``/usr/local/lib/vec0.so``, etc.)
    """
    # 1. Explicit override
    env_path = os.environ.get(_SQLITE_VEC_ENV)
    if env_path and Path(env_path).exists():
        return env_path

    # 2. PyPI package
    try:
        import sqlite_vec

        result: str = sqlite_vec.loadable_path()
        return result
    except (ImportError, AttributeError):
        pass

    # 3. System paths
    system_paths = [
        "/usr/local/lib/vec0.so",
        "/usr/lib/sqlite3/vec0.so",
        "/usr/lib/vec0.so",
    ]
    for path in system_paths:
        if Path(path).exists():
            return path

    return None


def load_extensions(db: sqlite3.Connection) -> None:
    """
    Load the sqlite-vec extension into an open SQLite connection.

    Since usearch replaced sqlite-vec as the primary vector store (Sprint 17),
    this is now best-effort: logs a warning if the extension is unavailable
    instead of raising. The embed pipeline writes to usearch; sqlite-vec
    is only needed for legacy vectors_vec access.
    """
    vec_path = _find_sqlite_vec()
    if not vec_path:
        logger.info("sqlite-vec extension not found — using usearch for vector operations")
        return

    try:
        db.enable_load_extension(True)
        # SQLite strips the platform suffix (.so / .dylib) itself
        load_path = vec_path
        for suffix in (".so", ".dylib", ".dll"):
            if load_path.endswith(suffix):
                load_path = load_path.removesuffix(suffix)
                break
        db.load_extension(load_path)
        db.enable_load_extension(False)
    except Exception as exc:
        logger.warning("sqlite-vec extension failed to load: %s — using usearch", exc)


def open_db(path: Path | None = None, *, extensions: bool = True) -> sqlite3.Connection:
    """
    Open (or create) the kairix SQLite database.

    Args:
        path:       Explicit path. Defaults to ``get_db_path()``.
        extensions: If True, load sqlite-vec. Set False for FTS-only operations.

    Returns:
        An open ``sqlite3.Connection`` with WAL mode enabled.
    """
    if path is None:
        path = get_db_path()

    # Ensure parent directory exists for fresh installs
    path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    if extensions:
        load_extensions(db)

    return db
