"""
Kairix storage layer — owns the SQLite database and FTS5 index.

Kairix maintains its own database at ``<data-dir>/index.sqlite`` — the
persistent FHS/XDG data dir (``/var/lib/kairix`` in the container, baked as
``KAIRIX_DB_PATH``; ``~/.local/share/kairix`` for a user install), NOT the
regenerable cache (configurable via ``KAIRIX_DB_PATH``). This matches
``kairix.paths.db_path`` / ``index_path`` so the worker and the embed/search
CLIs resolve the SAME index file on a no-env install (#447 / PLA-276).

Public API:
  - get_db_path()       — resolve the database file path
  - open_db()           — open a connection with WAL mode
"""

import logging
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from kairix.paths import embed_vector_dims as _embed_vector_dims

logger = logging.getLogger(__name__)

# Environment variable for explicit DB path override
_DB_PATH_ENV = "KAIRIX_DB_PATH"

# Embedding dimensions — configurable via KAIRIX_EMBED_DIMS. The env read
# lives in kairix.paths.embed_vector_dims (F4: env-reads stay in paths.py).
EMBED_VECTOR_DIMS = _embed_vector_dims()


def get_db_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """
    Resolve the kairix database path.

    Search order:
      1. ``KAIRIX_DB_PATH`` environment variable (explicit override)
      2. ``<home>/.local/share/kairix/index.sqlite`` — the persistent XDG
         data dir (default user-install location)

    The default lands in the persistent DATA dir, not the regenerable cache:
    the index is the source of truth (FTS5 + content_vectors), and resolving
    it under the cache dir let cache eviction / a non-persistent cache mount
    silently drop it. This mirrors ``kairix.paths.db_path`` /
    ``default_data_dir`` so the worker and the embed/search CLIs agree on a
    no-env install (#447 / PLA-276). On the standard container deploy
    ``KAIRIX_DB_PATH=/var/lib/kairix/index.sqlite`` is baked, so the override
    governs there.

    Returns the path (which may not exist yet for fresh installs).

    ``env`` and ``home`` are DI seams; tests pass an explicit mapping +
    home directory rather than monkeypatching the process environment.
    """
    if env is None:
        env = os.environ
    if home is None:
        home = Path.home()

    # 1. Explicit override — return it whether or not it exists yet; the
    #    caller creates it on first run (e.g. kairix scan / embed).
    env_path = env.get(_DB_PATH_ENV)
    if env_path:
        return Path(env_path)

    # 2. Default kairix location — persistent XDG data dir (not the cache).
    return home / ".local" / "share" / "kairix" / "index.sqlite"


def open_db(path: Path | None = None) -> sqlite3.Connection:
    """
    Open (or create) the kairix SQLite database.

    Args:
        path: Explicit path. Defaults to ``get_db_path()``.

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

    return db
