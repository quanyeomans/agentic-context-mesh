"""Pipeline-cache marker file (#411 Phase 2).

The pipeline itself can't be persisted across processes — pipelines own
live SQLite handles, HNSW indexes, and OAuth tokens. What we persist
instead is a marker: "the configuration with hash X was last built at
time T". A cold CLI start reads this marker and uses the recorded
cfg_hash to scope its query/prep persistent caches; if the on-disk
cfg_hash mismatches the one the current process resolves to, the cache
rows are silently dropped (effective invalidation).

The marker is intentionally minimal — one row per (cfg_hash). Storing
zero richness here (no per-config pipeline metadata) keeps the marker
forward-compatible: a kairix upgrade that adds a new pipeline component
doesn't need to bump this schema.

Stored as plain TEXT in a tiny SQLite file under
:func:`kairix.paths.pipeline_cache_path`. No binary blob formats, no
arbitrary-code-load surface.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "1"

_TABLE = "pipeline_cache_marker"
_META_TABLE = "pipeline_cache_marker_meta"

_CREATE_META_SQL = f"CREATE TABLE IF NOT EXISTS {_META_TABLE} (  key   TEXT PRIMARY KEY,  value TEXT NOT NULL)"
_CREATE_SQL = f"CREATE TABLE IF NOT EXISTS {_TABLE} (  cfg_hash TEXT PRIMARY KEY,  built_at REAL NOT NULL)"
_UPSERT_SQL = f"INSERT OR REPLACE INTO {_TABLE} (cfg_hash, built_at) VALUES (?, ?)"
_SELECT_LAST_SQL = f"SELECT cfg_hash, built_at FROM {_TABLE} ORDER BY built_at DESC LIMIT 1"
_TRUNCATE_SQL = f"DELETE FROM {_TABLE}"
_META_GET_SQL = f"SELECT value FROM {_META_TABLE} WHERE key = ?"
_META_UPSERT_SQL = f"INSERT OR REPLACE INTO {_META_TABLE} (key, value) VALUES (?, ?)"


def compute_cfg_hash(cfg: Any) -> str:
    """Compute a stable short hash for a ``RetrievalConfig``-like dataclass.

    The cfg_hash is the scoping key for the per-cfg persistent caches.
    Any field change in the dataclass (provider swap, fusion strategy,
    rrf_k, boost-config tweaks…) produces a new hash, which
    invalidates previously-persisted query / prep cache rows.

    Stable across processes: uses :func:`dataclasses.asdict` + sorted
    JSON-equivalent ``repr`` so the same field values always hash to
    the same string.

    Returns the empty string for non-dataclass inputs — callers that
    can't produce a hash get cfg-scoping-disabled (every row collapses
    to the empty-string scope). Defensive default for the tests that
    construct caches without a config.
    """
    if not is_dataclass(cfg) or isinstance(cfg, type):
        return ""
    try:
        # Sort field names so the same dataclass with reordered field
        # defs hashes to the same value.
        d = asdict(cfg)
        # ``repr`` keeps sort-order stable when we pass sort_keys=True
        # to json, but we want a representation that handles nested
        # tuples + enums uniformly. Recursive normalisation is simplest.
        normalised = _normalise(d)
        return hashlib.sha256(repr(normalised).encode("utf-8")).hexdigest()[:32]
    except (TypeError, ValueError) as exc:
        logger.debug("compute_cfg_hash: falling back to empty hash. cause: %s", exc)
        return ""


def _normalise(obj: Any) -> Any:
    """Stable ordering for nested dict/list/tuple values."""
    if isinstance(obj, dict):
        return [(k, _normalise(obj[k])) for k in sorted(obj.keys())]
    if isinstance(obj, list | tuple):
        return [_normalise(item) for item in obj]
    return obj


class PipelineCacheMarker:
    """SQLite-backed marker recording the last cfg_hash that was built.

    Single-row marker (technically: multi-row keyed on cfg_hash; the
    most recent ``built_at`` is the marker the cold-start consults).
    Tiny, lazy-opened, defensive against disk failures.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path: Path | None = Path(path) if path is not None else None
        self._conn: sqlite3.Connection | None = None

    def _ensure_open(self) -> sqlite3.Connection | None:
        """Lazy-open the SQLite file on first access."""
        if self._conn is not None:
            return self._conn
        if self._path is None:
            return None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # pipeline-build marker file (#411 Phase 2). Tiny (one row per cfg_hash);
            # F77-allow: kairix-user-owned data dir; not a writer-coordinator concern.
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.execute(_CREATE_META_SQL)
            conn.execute(_CREATE_SQL)
            conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "PipelineCacheMarker: failed to open persistence file %s — running marker-less. cause: %s",
                self._path,
                exc,
            )
            return None
        self._conn = conn
        try:
            self._check_schema_version()
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "PipelineCacheMarker: schema-version probe failed — running marker-less. cause: %s",
                exc,
            )
            self._conn = None
        return self._conn

    def _check_schema_version(self) -> None:
        if self._conn is None:
            return
        cursor = self._conn.execute(_META_GET_SQL, ("schema_version",))
        row = cursor.fetchone()
        stored = row[0] if row else None
        if stored == _SCHEMA_VERSION:
            return
        with self._conn:
            self._conn.execute(_TRUNCATE_SQL)
            self._conn.execute(_META_UPSERT_SQL, ("schema_version", _SCHEMA_VERSION))

    @property
    def path(self) -> Path | None:
        """On-disk persistence path, or ``None`` when marker-less."""
        return self._path

    def record(self, cfg_hash: str, built_at: float) -> None:
        """Persist that the pipeline keyed on ``cfg_hash`` was built at ``built_at``.

        Idempotent: replacing an existing row updates ``built_at`` only.
        Defensive against disk failures — never raises into the caller.
        """
        conn = self._ensure_open()
        if conn is None:
            return
        try:
            with conn:
                conn.execute(_UPSERT_SQL, (cfg_hash, built_at))
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "PipelineCacheMarker: record failed for cfg_hash=%s. cause: %s",
                cfg_hash,
                exc,
            )

    def last(self) -> tuple[str, float] | None:
        """Return the (cfg_hash, built_at) of the most recently recorded build.

        ``None`` when no rows have been written or the file is
        unreadable. Cold CLI starts call this once at startup to decide
        whether the persistent query / prep caches' rows match the
        current cfg.
        """
        conn = self._ensure_open()
        if conn is None:
            return None
        try:
            cursor = conn.execute(_SELECT_LAST_SQL)
            row = cursor.fetchone()
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "PipelineCacheMarker: last() read failed. cause: %s",
                exc,
            )
            return None
        if row is None:
            return None
        return (str(row[0]), float(row[1]))

    def close(self) -> None:
        """Close the SQLite connection (idempotent)."""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


__all__ = ["PipelineCacheMarker", "compute_cfg_hash"]
