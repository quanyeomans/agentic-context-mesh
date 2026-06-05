"""Centralised path resolution for kairix.

Every module that needs a file path imports from here instead of
hardcoding defaults. Paths are resolved once and cached.

Resolution order (highest wins):
  1. Environment variables (KAIRIX_DOCUMENT_ROOT, KAIRIX_DB_PATH, etc.)
     - KAIRIX_DOCUMENT_ROOT is the canonical env var
  2. Config file paths: section (kairix.config.yaml)
  3. Platform-aware defaults (macOS, Linux, Docker)
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# XDG-style user cache directory name (Path.home() / _USER_CACHE_DIR / "kairix").
# Centralised so the path is the same wherever a non-Docker, non-service install
# resolves a cache location.
_USER_CACHE_DIR = ".cache"

# Canonical agent-knowledge directory under the document root.
# Hosts agent memory subtrees and curator-managed config files (notably
# ``_entity-overrides.md``). Extracted to satisfy F17 — three resolvers
# below need to compose this segment and the literal string is otherwise
# duplicated across them.
_AGENT_KNOWLEDGE_DIR = "04-Agent-Knowledge"

# Env-var name for the operator-supplied config file path. Centralised
# so the literal isn't duplicated across the three resolvers that need
# to honour the operator override (F17 hygiene — appears ≥3 times in
# this module once the feature-flag scaffold's config-overlay reader
# lands).
_KAIRIX_CONFIG_PATH_ENV = "KAIRIX_CONFIG_PATH"


def is_docker_runtime_check() -> bool:
    """Detect if running inside a Docker container."""
    return (
        os.path.exists("/.dockerenv")
        or os.environ.get("KAIRIX_DOCKER", "") == "1"
        or os.environ.get("container", "") != ""
    )


def is_service_install() -> bool:
    """Detect if kairix was installed as a system service (/opt/kairix)."""
    return Path("/opt/kairix/.venv").exists()


def default_document_root() -> Path:
    """Platform-appropriate default document store location.

    Docker: /data/documents (bind mount from host)
    Server: /var/lib/kairix/documents (admin configures)
    User (all platforms): ~/Documents (most common document location)
    """
    if is_docker_runtime_check():
        return Path("/data/documents")
    if is_service_install():
        return Path("/var/lib/kairix/documents")
    return Path.home() / "Documents"


def default_data_dir(platform: str = sys.platform) -> Path:
    """Platform-appropriate data directory for DB, vectors, and state.

    Docker: /data/kairix
    Server: /var/lib/kairix
    Linux/macOS user: ~/.local/share/kairix (XDG_DATA_HOME)
    Windows user: %LOCALAPPDATA%/kairix

    ``platform`` defaults to ``sys.platform`` and is exposed as a
    parameter so unit tests can drive the Windows branch on any host
    without patching ``kairix.paths.sys``.
    """
    if is_docker_runtime_check():
        return Path("/data/kairix")
    if is_service_install():
        return Path("/var/lib/kairix")
    if platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "kairix"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "kairix"
    return Path.home() / ".local" / "share" / "kairix"


def default_cache_dir(platform: str = sys.platform) -> Path:
    """Platform-appropriate cache directory for temporary data.

    Docker: /data/kairix (same as data dir)
    Server: /var/cache/kairix
    Linux/macOS user: ~/.cache/kairix (XDG_CACHE_HOME)
    Windows user: %LOCALAPPDATA%/kairix/cache

    ``platform`` defaults to ``sys.platform``; injectable for the same
    reason as ``default_data_dir``.
    """
    if is_docker_runtime_check():
        return Path("/data/kairix")
    if is_service_install():
        return Path("/var/cache/kairix")
    if platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "kairix" / "cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "kairix"
    return Path.home() / _USER_CACHE_DIR / "kairix"


def default_workspace_root() -> Path:
    """Platform-appropriate workspace root for agent memory logs."""
    if is_docker_runtime_check():
        return Path("/data/workspaces")
    if is_service_install():
        return Path("/data/workspaces")
    return Path.home() / ".kairix" / "workspaces"


@dataclass(frozen=True)
class KairixPaths:
    """Resolved paths for a kairix deployment.

    Use KairixPaths.resolve() to get paths based on your environment.
    All paths are absolute.
    """

    document_root: Path
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
    cache_dir = default_cache_dir()

    # Try loading paths from config file
    config_paths = load_paths_from_config()

    document_root = Path(
        os.environ.get("KAIRIX_DOCUMENT_ROOT") or config_paths.get("document_root") or str(default_document_root())
    ).expanduser()

    db_path = Path(
        os.environ.get("KAIRIX_DB_PATH") or config_paths.get("db_path") or str(cache_dir / "index.sqlite")
    ).expanduser()

    log_dir = Path(
        os.environ.get("KAIRIX_LOG_DIR")
        or os.environ.get("LOG_DIR")
        or config_paths.get("log_dir")
        or str(cache_dir / "logs")
    ).expanduser()

    workspace_root = Path(
        os.environ.get("KAIRIX_WORKSPACE_ROOT") or config_paths.get("workspace_root") or str(default_workspace_root())
    ).expanduser()

    return KairixPaths(
        document_root=document_root,
        db_path=db_path,
        log_dir=log_dir,
        workspace_root=workspace_root,
    )


def load_paths_from_config() -> dict[str, str]:
    """Load the paths: section from kairix.config.yaml if it exists."""
    config_path = os.environ.get(_KAIRIX_CONFIG_PATH_ENV) or "kairix.config.yaml"
    try:
        import yaml

        p = Path(config_path).expanduser()
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f) or {}
            result: dict[str, str] = data.get("paths", {})
            return result
    except Exception:  # noqa: S110 — graceful fallback when config is missing or malformed
        pass
    return {}


def clear_cache() -> None:
    """Clear the cached path resolution. Call after changing env vars in tests."""
    _resolve_cached.cache_clear()


# Convenience functions — import these directly instead of calling KairixPaths.resolve()


def document_root() -> Path:
    """Return the document store root path."""
    return KairixPaths.resolve().document_root


def reference_library_root() -> Path:
    """Reference library root — ships inside the container at /opt/kairix/reference-library."""
    return Path(os.environ.get("KAIRIX_REFLIB_ROOT", "reference-library"))


def resolve_first_existing_dir(
    override: str | None,
    candidates: list[Path],
    fallback: Path,
) -> Path:
    """Return the first usable directory from the resolution chain.

    Used by ``bundled_suites_root`` (and any future shipped-asset
    resolver that needs the same env-override → candidate-list → CWD
    fallback semantics).

    Args:
        override: When non-empty, returned as a ``Path`` immediately.
                  A misconfigured operator override should surface as a
                  downstream ``FileNotFoundError`` rather than silently
                  fall through to a default.
        candidates: Ordered list of paths; the first one whose
                  ``is_dir()`` returns True wins.
        fallback: Returned when ``override`` is empty and no candidate
                  exists on disk. Typically the legacy CWD-relative
                  path so behaviour from before the resolver existed is
                  preserved.

    The helper is pure (no env reads of its own) so tests can drive it
    with crafted ``tmp_path`` candidate lists — no env-var monkeypatch
    needed (F2-clean).
    """
    if override:
        return Path(override)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return fallback


def bundled_suites_root() -> Path:
    """Resolve the bundled benchmark suites root.

    Resolution order (first existing path wins; the env-var override
    wins even if its target is missing, so misconfigurations surface as
    explicit ``FileNotFoundError`` downstream rather than silently
    using a fallback):

      1. ``$KAIRIX_SUITES_ROOT`` — operator override.
      2. ``<repo-root>/suites/`` — when running from a kairix source
         checkout; preserves the dev UX where ``cd`` to the repo finds
         ``./suites/``. Derived from the kairix package location
         (``Path(__file__).parent.parent`` = repo root).
      3. ``/opt/kairix/suites/`` — canonical install path the Docker
         image ships suites at. Closes #268: the host wrapper does
         ``docker exec`` into the container, where the CWD is unrelated
         to where suites live.
      4. ``./suites/`` — final CWD fallback (legacy behaviour).
    """
    repo_root_suites = Path(__file__).resolve().parent.parent / "suites"
    installed_suites = Path("/opt/kairix/suites")
    return resolve_first_existing_dir(
        override=os.environ.get("KAIRIX_SUITES_ROOT"),
        candidates=[repo_root_suites, installed_suites],
        fallback=Path("suites"),
    )


def worker_state_path() -> Path:
    """Path to the worker state JSON (#224). Sits in the kairix data dir so
    ``docker compose down/up`` preserves restart_count across worker restarts."""
    return default_data_dir() / "worker-state.json"


def worker_pause_flag_path() -> Path:
    """Touch-file checked by the worker each loop iteration (#224 phase 4).

    When present, the worker enters WorkerPhase.PAUSED until the flag is
    removed. ``kairix worker pause/resume`` toggles the file's existence.
    """
    return default_data_dir() / ".worker-paused"


def maintenance_skip_noop_threshold() -> int:
    """#224 phase 2 — number of consecutive no-op embed cycles after which
    the worker also skips the three maintenance scans (entity_seed,
    health_check, wikilinks_inject).

    When embed runs find nothing changed N times in a row, the maintenance
    scans are pointless work; skipping them lets a long-idle shared host
    drop to near-zero CPU/IO until the next document change. Reads
    ``KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD`` (int) — default 10. F4
    keeps the env read centralised here in paths.py.
    """
    raw = os.environ.get("KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD")
    if raw is None:
        return 10
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD=%r is not an int; using default 10",
            raw,
        )
        return 10


def maintenance_retention_days() -> int:
    """KFEAT-021 Phase 1 — how long pruned vectors stay in the soft-delete table.

    The :class:`kairix.core.maintenance.MaintenanceScheduler` moves orphan
    ``content_vectors`` rows to ``content_vectors_pruned`` before any hard
    delete. Rows whose ``pruned_at`` timestamp is older than this many days
    are hard-deleted on the next tick — the window is the operator's
    recovery affordance.

    Reads ``KAIRIX_MAINTENANCE_RETENTION_DAYS`` (int) — default 7. F4
    keeps the env read centralised here in paths.py.
    """
    raw = os.environ.get("KAIRIX_MAINTENANCE_RETENTION_DAYS")
    if raw is None:
        return 7
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_MAINTENANCE_RETENTION_DAYS=%r is not an int; using default 7",
            raw,
        )
        return 7
    if parsed < 0:
        logger.warning(
            "KAIRIX_MAINTENANCE_RETENTION_DAYS=%r is negative; using default 7",
            raw,
        )
        return 7
    return parsed


def bronze_ttl_days() -> int:
    """#316 — TTL for bronze raw blobs when ``bronze_ttl_gc`` flag is ON.

    The :class:`kairix.core.connectors.bronze.FilesystemBronzeStore`
    :meth:`gc_aged` step deletes bronze_records rows + raw blobs older
    than this many days on every maintenance tick (when the
    ``bronze_ttl_gc`` flag is ON). Bounds bronze growth — without it,
    the SharePoint dogfood corpus accumulated 36 GB of correctly-
    registered blobs that the orphan reaper (#318) couldn't touch.

    Reads ``KAIRIX_BRONZE_TTL_DAYS`` (int) — default 7. F4 keeps the
    env read centralised here in paths.py.
    """
    raw = os.environ.get("KAIRIX_BRONZE_TTL_DAYS")
    if raw is None:
        return 7
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_BRONZE_TTL_DAYS=%r is not an int; using default 7",
            raw,
        )
        return 7
    if parsed < 0:
        logger.warning(
            "KAIRIX_BRONZE_TTL_DAYS=%r is negative; using default 7",
            raw,
        )
        return 7
    return parsed


def maintenance_interval_seconds() -> int:
    """KFEAT-021 Phase 1 — seconds between maintenance ticks in the worker loop.

    When the ``maintenance_loop`` feature flag is ON, the worker fires a
    :func:`kairix.core.maintenance.scheduler.MaintenanceScheduler.tick`
    every N seconds. Default 86400 (24 h) fits low-write deployments;
    busier ones might tune this to 4-6 hours via the env override.

    Reads ``KAIRIX_MAINTENANCE_INTERVAL_S`` (int seconds) — default
    86400. F4 keeps the env read centralised here in paths.py.
    """
    raw = os.environ.get("KAIRIX_MAINTENANCE_INTERVAL_S")
    if raw is None:
        return 86400
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_MAINTENANCE_INTERVAL_S=%r is not an int; using default 86400",
            raw,
        )
        return 86400
    if parsed <= 0:
        logger.warning(
            "KAIRIX_MAINTENANCE_INTERVAL_S=%r is not positive; using default 86400",
            raw,
        )
        return 86400
    return parsed


def trace_enabled() -> bool:
    """Return True when ``KAIRIX_TRACE=1`` opts into structured pipeline diagnostics.

    Off by default; production stays quiet. Operators investigating a
    retrieval-vs-synthesis regression set the env var, re-run, and read
    the per-stage counter logs. Centralised here per F4 so KAIRIX_*
    reads stay at the paths.py boundary.
    """
    return os.environ.get("KAIRIX_TRACE") == "1"


def db_path() -> Path:
    """Get the database path."""
    return KairixPaths.resolve().db_path


def log_dir() -> Path:
    """Get the log directory path."""
    return KairixPaths.resolve().log_dir


def workspace_root() -> Path:
    """Get the workspace root path."""
    return KairixPaths.resolve().workspace_root


def embedding_cache_path() -> Path:
    """Resolve the SQLite-backed persistent embedding cache path.

    Lives under ``<document_root>/.kairix/cache/embedding_cache.sqlite``
    so it travels with the document store across deployments and is
    never mixed with the canonical ``index.sqlite``. Reuses the cached
    ``document_root()`` resolution so per-process overrides flow through.

    See :mod:`kairix.core.embed.embedding_cache` for cache shape +
    invariants. F4-clean — the env read for ``KAIRIX_DOCUMENT_ROOT``
    happens inside ``document_root`` at the paths boundary.
    """
    return document_root() / ".kairix" / "cache" / "embedding_cache.sqlite"


def embed_cache_path() -> Path:
    """Resolve the SQLite-backed persistent transport-layer embed cache path.

    Distinct from :func:`embedding_cache_path`:

      - :func:`embedding_cache_path` (``embedding_cache.sqlite`` under
        ``<document_root>/.kairix/cache/``) caches **chunk** embeddings
        keyed on ``(model, dimension, chunk_hash)`` — the embed-ingest
        pipeline's restart-resilient store.
      - :func:`embed_cache_path` (``embed_cache.sqlite`` under
        :func:`data_dir`) caches **query** embeddings keyed on the
        normalised query text — the transport-layer cache that sits
        in front of every search-time embed call.

    Lives under :func:`data_dir` (``/data/kairix`` in Docker;
    ``/var/lib/kairix`` for service installs; XDG data dir for user
    installs) so it travels with the kairix data volume across
    ``docker compose down/up`` cycles. Closes #391: the previous
    in-process LRU implementation lost its entries on every container
    restart, so every release fan-out re-paid the ~250-500 ms Azure
    embed roundtrip for every repeat query.

    F4-clean — the env read for ``KAIRIX_DATA_DIR`` happens inside
    :func:`data_dir` at the paths boundary.
    """
    return data_dir() / "embed_cache.sqlite"


def pipeline_cache_path() -> Path:
    """Resolve the SQLite-backed marker file for the pipeline build cache (#411 Phase 2).

    The pipeline itself can't be persisted — it owns live SQLite handles,
    HNSW indexes, and OAuth tokens. What we persist instead is a marker:
    "config X was last built at time T". The next process consults this
    marker to validate that any persisted query / prep cache entries are
    still keyed against the same pipeline shape.

    Lives under :func:`data_dir` next to :func:`embed_cache_path`, so a
    ``docker compose restart`` (and the cold CLI starts that follow when
    no MCP is up) inherits the marker the previous warm process wrote.

    F4-clean — env reads stay inside :func:`data_dir`.
    """
    return data_dir() / "pipeline_cache.sqlite"


def query_cache_path() -> Path:
    """Resolve the SQLite-backed persistent query-result cache path (#411 Phase 2).

    Sibling to :func:`embed_cache_path`. The query-result cache holds
    full :class:`SearchResult` envelopes keyed on
    ``(cfg_hash, query_hash)``; a cold CLI start that finds a non-expired
    row for the same cfg + query skips the ~1-3 s search dispatch
    entirely.

    Lives under :func:`data_dir` so the file rides the kairix data
    volume across restarts.

    F4-clean — env reads stay inside :func:`data_dir`.
    """
    return data_dir() / "query_cache.sqlite"


def prep_cache_path() -> Path:
    """Resolve the SQLite-backed persistent prep-summary cache path (#411 Phase 2).

    Sibling to :func:`embed_cache_path` and :func:`query_cache_path`.
    The prep cache holds LLM-synthesised summaries keyed on
    ``(cfg_hash, prep_key_hash)`` (where ``prep_key_hash`` folds
    ``(query, tier, context)``). A cold CLI ``kairix prep`` that hits
    a persisted row skips the 2-4 s LLM synthesis call.

    Lives under :func:`data_dir` so the file rides the kairix data
    volume across restarts.

    F4-clean — env reads stay inside :func:`data_dir`.
    """
    return data_dir() / "prep_cache.sqlite"


def summaries_db_path() -> Path:
    """Get the summaries database path.

    Configurable via KAIRIX_SUMMARIES_DB env var.
    Default: ~/.cache/kairix/summaries.db
    """
    return Path(
        os.environ.get(
            "KAIRIX_SUMMARIES_DB",
            str(Path.home() / _USER_CACHE_DIR / "kairix" / "summaries.db"),
        )
    )


def set_agent_memory_root_override(root: str) -> None:
    """Set the ``KAIRIX_AGENT_MEMORY_ROOT`` env var so subsequent calls to
    :func:`agent_memory_path` see the override.

    Used by CLI entry points (``kairix brief --memory-root ...``) to thread
    an operator-supplied memory root through the use case. The write
    lives here so F4's "env reads stay in paths.py" gate covers both
    sides of the env-var boundary.
    """
    os.environ["KAIRIX_AGENT_MEMORY_ROOT"] = root


def read_int_env(name: str, *, default: int) -> int:
    """Read an int from the named env var, falling back to ``default``.

    Centralised here so callers needing tunable int knobs do not scatter
    ``os.environ.get`` reads across production modules (F4). Malformed
    values log a warning and fall back to ``default`` — the same
    defensive policy used by the other typed env-var readers above.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an int; using default %d", name, raw, default)
        return default


def read_float_env(name: str, *, default: float) -> float:
    """Read a float from the named env var, falling back to ``default``.

    Counterpart to :func:`read_int_env` for float-typed knobs (e.g.
    cache TTLs in seconds). F4-clean — env reads stay in this module.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a float; using default %f", name, raw, default)
        return default


def embed_vector_dims(default: int = 1536) -> int:
    """Embedding vector dimensions — configurable via ``KAIRIX_EMBED_DIMS``.

    Returns the int value of the env var, or ``default`` when unset.
    Reads at call time (not import time) so test fakes that mutate the
    environment win — but production code should treat the value as fixed
    for the lifetime of the process.
    """
    raw = os.environ.get("KAIRIX_EMBED_DIMS")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_EMBED_DIMS=%r is not an int; using default %d",
            raw,
            default,
        )
        return default


def is_docker_env() -> bool:
    """Return True when running inside a Docker container.

    Detection: ``/.dockerenv`` exists, ``KAIRIX_DOCKER=1``, or the generic
    ``container`` env var is set. Used by factories that want to swap log
    paths between container and host layouts.
    """
    return is_docker_runtime_check()


def log_queries_enabled() -> bool:
    """Privacy-gated query-log toggle: ``KAIRIX_LOG_QUERIES=1`` enables the
    raw-query JSONL emitter. Off by default."""
    return os.environ.get("KAIRIX_LOG_QUERIES") == "1"


def extra_collections() -> list[str]:
    """Operator-supplied extra collection names — ad-hoc additions when
    there's no full config file. Parses ``KAIRIX_EXTRA_COLLECTIONS`` as a
    comma-separated list and returns the non-empty entries.
    """
    raw = os.environ.get("KAIRIX_EXTRA_COLLECTIONS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def config_path_override() -> str | None:
    """Explicit config path from ``KAIRIX_CONFIG_PATH``, or ``None`` when unset.

    The single source of truth for the env-var override consumed by
    ``kairix.core.search.config_loader.resolve_config_path`` and
    ``load_paths_from_config`` (which still reads via ``os.environ`` to
    avoid a circular import inside this module).
    """
    value = os.environ.get(_KAIRIX_CONFIG_PATH_ENV)
    return value if value else None


def boards_dir_override() -> Path | None:
    """Operator override for the Kanban boards directory.

    Reads ``KAIRIX_BOARDS_DIR``. Returns ``None`` when unset so callers can
    fall back to ``document_root() / "01-Projects" / "Boards"``.
    """
    raw = os.environ.get("KAIRIX_BOARDS_DIR")
    return Path(raw) if raw else None


def provider_name() -> str | None:
    """Configured provider plugin name from ``kairix.config.yaml``, or ``None``.

    Reads the top-level ``provider:`` field from the operator's
    ``kairix.config.yaml``. Returns the stripped string when present
    and non-empty; returns ``None`` otherwise so callers that depend
    on a configured plugin can surface a typed
    ``ProviderNotRegistered``-shaped error themselves.

    The seam moved from ``KAIRIX_PROVIDER`` (env var) to the config
    file in v2026.5.17 — operators pick a plugin in config; the plugin
    owns its own credential-retrieval pattern (Azure → Key Vault;
    AWS → Secrets Manager; etc.) so the secrets surface is shaped by
    the plugin, not the env vocabulary. See
    ``docs/architecture/provider-plugin-architecture.md``.

    Lives in :mod:`kairix.paths` so the file-system read stays at the
    F4 boundary even when the underlying source is a yaml file rather
    than ``os.environ``. The import lives inside the function to keep
    ``kairix.paths`` free of a module-level dependency on the
    retrieval-config loader (which itself imports ``kairix.paths`` for
    ``config_path_override``).
    """
    # Lazy import — avoid circular dependency with config_loader, which
    # imports ``config_path_override`` from this module.
    from kairix.core.search.config_loader import load_config

    try:
        cfg = load_config()
    except Exception as exc:
        # YAML parse errors / ConfigValidationError shouldn't crash
        # operator-facing probes; surface ``None`` and let the caller
        # render the actionable affordance.
        logger.warning("provider_name: failed to load kairix.config.yaml — %s", exc)
        return None
    value = getattr(cfg, "provider", None)
    return value if value else None


def azure_api_version(default: str = "2024-12-01-preview") -> str:
    """Azure OpenAI API version — configurable via ``KAIRIX_AZURE_API_VERSION``."""
    return os.environ.get("KAIRIX_AZURE_API_VERSION", default)


def bedrock_region_override() -> str | None:
    """AWS region for the bedrock provider — configurable via ``KAIRIX_BEDROCK_REGION``.

    Overrides whatever the boto3 default credential chain picked
    (``AWS_DEFAULT_REGION`` / ``~/.aws/config``) so operators can pin
    the Bedrock inference region distinct from their AWS control-plane
    region. Returns ``None`` when unset; the bedrock plugin then falls
    back to boto3's resolved region. Lives in :mod:`kairix.paths` per
    F4 — no other module may read ``KAIRIX_*`` env vars.
    """
    value = os.environ.get("KAIRIX_BEDROCK_REGION")
    return value if value else None


def bedrock_embed_model(default: str = "amazon.titan-embed-text-v2:0") -> str:
    """Bedrock embed model id — configurable via ``KAIRIX_BEDROCK_EMBED_MODEL``.

    Defaults to Amazon Titan Text Embeddings V2. Cohere embed models on
    Bedrock (``cohere.embed-*``) are also supported by the plugin's
    body-shape dispatch. Lives in :mod:`kairix.paths` per F4.
    """
    return os.environ.get("KAIRIX_BEDROCK_EMBED_MODEL", default)


def bedrock_chat_model(default: str = "anthropic.claude-3-5-sonnet-20241022-v2:0") -> str:
    """Bedrock chat model id — configurable via ``KAIRIX_BEDROCK_CHAT_MODEL``.

    Defaults to Anthropic Claude 3.5 Sonnet on Bedrock. Only
    ``anthropic.*`` model ids are wired for chat at present; non-
    Anthropic ids surface as a typed ``ClientError`` from
    :meth:`kairix.providers.bedrock.BedrockProvider.chat`. Lives in
    :mod:`kairix.paths` per F4.
    """
    return os.environ.get("KAIRIX_BEDROCK_CHAT_MODEL", default)


def embed_pool_size(default: int = 20) -> int:
    """Max concurrent HTTP connections to the embed provider.

    Configurable via ``KAIRIX_EMBED_POOL_SIZE``. Sized for kairix's teaming
    concurrency profile (20 agents, 5-15 sustained) with headroom. Invalid
    values fall back to ``default`` with a logged warning so a bad operator
    secret can't crash the embed dispatch stage. Read at call time so the
    operator can rotate the value via Key Vault without restarting.
    """
    raw = os.environ.get("KAIRIX_EMBED_POOL_SIZE")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_EMBED_POOL_SIZE=%r is not an int; using default %d",
            raw,
            default,
        )
        return default


def embed_pool_keepalive(default: int = 10) -> int:
    """Max idle HTTP connections kept warm against the embed provider.

    Configurable via ``KAIRIX_EMBED_POOL_KEEPALIVE``. Balances connection
    reuse against socket churn under burst load. Invalid values fall back
    to ``default`` with a logged warning.
    """
    raw = os.environ.get("KAIRIX_EMBED_POOL_KEEPALIVE")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_EMBED_POOL_KEEPALIVE=%r is not an int; using default %d",
            raw,
            default,
        )
        return default


def embed_pool_expiry_s(default: float = 30.0) -> float:
    """Idle-connection expiry (seconds) for the embed-provider pool.

    Configurable via ``KAIRIX_EMBED_POOL_EXPIRY_S``. Invalid values fall
    back to ``default`` with a logged warning.
    """
    raw = os.environ.get("KAIRIX_EMBED_POOL_EXPIRY_S")
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_EMBED_POOL_EXPIRY_S=%r is not a float; using default %s",
            raw,
            default,
        )
        return default


def embed_coalesce_window_ms(default: int = 50) -> int:
    """Coalesce window (ms) for the embed request coalescer (#288).

    Configurable via ``KAIRIX_EMBED_COALESCE_WINDOW_MS``. Range 0-500;
    out-of-range values clamp to the bound. ``0`` disables the
    coalescer entirely — useful for low-concurrency deployments and
    debugging. Invalid (non-int) values fall back to ``default`` with
    a logged warning so a typo can't crash the embed dispatch stage.
    """
    raw = os.environ.get("KAIRIX_EMBED_COALESCE_WINDOW_MS")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_EMBED_COALESCE_WINDOW_MS=%r is not an int; using default %d",
            raw,
            default,
        )
        return default
    return max(0, min(500, value))


def embed_coalesce_max_batch(default: int = 16) -> int:
    """Max batch size for the embed request coalescer (#288).

    Configurable via ``KAIRIX_EMBED_COALESCE_MAX_BATCH``. Range 1-64.
    Invalid values fall back to ``default`` with a logged warning.
    """
    raw = os.environ.get("KAIRIX_EMBED_COALESCE_MAX_BATCH")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_EMBED_COALESCE_MAX_BATCH=%r is not an int; using default %d",
            raw,
            default,
        )
        return default
    return max(1, min(64, value))


def mcp_port(default: int = 8080) -> int:
    """Resolve the MCP server port from ``KAIRIX_MCP_PORT``, or ``default``.

    Used by both the MCP CLI's auto-detect path and the onboarding probe.
    """
    raw = os.environ.get("KAIRIX_MCP_PORT")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "KAIRIX_MCP_PORT=%r is not an int; using default %d",
            raw,
            default,
        )
        return default


def mcp_port_raw() -> str | None:
    """Raw ``KAIRIX_MCP_PORT`` env-var value, or ``None`` when unset.

    Use this when callers need to distinguish "operator set the env var"
    from "fell back to the default" — e.g. argparse-driven flag-vs-env
    precedence in ``kairix mcp serve``.
    """
    raw = os.environ.get("KAIRIX_MCP_PORT")
    return raw if raw else None


# Default MCP endpoint for the CLI-routes-via-MCP dispatcher (#411). The
# CLI does a sub-100ms HEAD probe against ``<endpoint>/healthz/ready``
# and, when responsive, routes subcommands through the warm MCP process
# instead of paying the cold-start cost in-process. F4 keeps the env
# read inside ``paths.py`` rather than scattering it through cli.py.
_DEFAULT_MCP_ENDPOINT = "http://localhost:8080/mcp"
_FALSY_FLAG_VALUES = frozenset({"0", "false", "off", "no"})


def mcp_endpoint(
    default: str = _DEFAULT_MCP_ENDPOINT,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the MCP server endpoint from ``KAIRIX_MCP_ENDPOINT``, or ``default``.

    Used by the CLI dispatcher (#411) to decide whether a warm MCP server
    is reachable. The endpoint is the streamable-HTTP base URL (e.g.
    ``http://localhost:8080/mcp``) — the dispatcher derives the readiness
    probe URL (``<endpoint-host>/healthz/ready``) from this.

    ``environ`` is the test seam: production callers leave it None and
    the function reads ``os.environ`` once. Tests pass an explicit dict
    so F2 (no ``monkeypatch.setenv("KAIRIX_*")``) stays clean — the env
    var read still lives inside paths.py (F4) but the test never has to
    mutate process env to exercise the parsing.
    """
    env = environ if environ is not None else os.environ
    raw = env.get("KAIRIX_MCP_ENDPOINT")
    if raw:
        return raw
    return default


def mcp_routing_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """Whether CLI-routes-via-MCP dispatch is enabled (#411).

    Defaults to True: when a warm MCP server is detected on
    :func:`mcp_endpoint`, subcommands route through it. Operators that
    want to disable the optimisation entirely (force every CLI call to
    run in-process) set ``KAIRIX_MCP_ROUTING=0``. Any other value (or
    unset) leaves the optimisation on.

    ``environ`` mirrors :func:`mcp_endpoint`'s test seam — production
    leaves it None; tests pass an explicit dict to stay F2-clean.
    """
    env = environ if environ is not None else os.environ
    raw = env.get("KAIRIX_MCP_ROUTING")
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY_FLAG_VALUES


def reflib_root_override() -> str | None:
    """Operator override for the reference-library root via
    ``KAIRIX_REFLIB_ROOT``. ``None`` when unset so callers can demand
    ``--reflib-root`` instead of falling back to a baked-in default."""
    value = os.environ.get("KAIRIX_REFLIB_ROOT")
    return value if value else None


def document_root_override() -> str | None:
    """Operator override for the document-root via ``KAIRIX_DOCUMENT_ROOT``.

    Mirrors :func:`reflib_root_override` semantics — returns ``None`` so
    CLI handlers can show a "required flag" message instead of silently
    using a default. The cached :func:`document_root` keeps its own
    independent read (with platform defaults) for non-CLI callers.
    """
    value = os.environ.get("KAIRIX_DOCUMENT_ROOT")
    return value if value else None


def data_dir() -> Path:
    """Public accessor for the platform-aware data dir.

    Wraps ``default_data_dir`` so other modules can centralise their
    "log / cache under the kairix data dir" path resolution without
    re-reading ``KAIRIX_DATA_DIR`` (or its legacy fallback) themselves.
    Honours ``KAIRIX_DATA_DIR`` when set — operators occasionally pin the
    data dir directly rather than via Docker / service detection.
    """
    raw = os.environ.get("KAIRIX_DATA_DIR")
    if raw:
        return Path(raw)
    return default_data_dir()


def monitor_log_path() -> Path:
    """Search-monitor JSONL log path.

    Reads ``KAIRIX_MONITOR_LOG`` directly, falling back to
    ``~/.cache/kairix/monitor.jsonl``. Kept as a separate helper from the
    platform-aware :func:`data_dir` because the legacy default predates the
    XDG-aware data-dir resolution and operators are wired to the old path.
    """
    raw = os.environ.get("KAIRIX_MONITOR_LOG")
    if raw:
        return Path(raw)
    return Path.home() / _USER_CACHE_DIR / "kairix" / "monitor.jsonl"


def search_log_path() -> Path:
    """Query/search-event JSONL log path.

    Order: ``KAIRIX_SEARCH_LOG`` → ``$KAIRIX_DATA_DIR/logs/search.jsonl`` →
    ``~/.cache/kairix/logs/search.jsonl``.
    """
    raw = os.environ.get("KAIRIX_SEARCH_LOG")
    if raw:
        return Path(raw)
    return data_dir() / "logs" / "search.jsonl"


def wikilinks_last_run_path() -> Path:
    """Touch-file recording the wikilinks-inject high-water timestamp.

    Lives under :func:`data_dir` (so ``KAIRIX_DATA_DIR`` overrides honour
    it) and is read by ``kairix wikilinks inject --changed``.
    """
    return data_dir() / "wikilinks-last-run"


def env_file_override() -> str | None:
    """Explicit env-file path for the deployment-check self-load step.

    Reads ``KAIRIX_ENV_FILE``. Returns ``None`` (not ``""``) when unset so
    callers can use ``is None`` / ``or`` without ambiguity.
    """
    value = os.environ.get("KAIRIX_ENV_FILE")
    return value if value else None


def warm_flag_path() -> Path:
    """Path to the cross-process warm-state flag — single env-read boundary
    for ``KAIRIX_WARM_FLAG_PATH``.

    The MCP server writes this flag when it finishes warming;
    ``kairix onboard ready`` (running as the docker healthcheck) reads it
    to decide whether ``docker compose up --wait`` can return.

    The flag lives under :func:`data_dir` — owned by the kairix process
    user, not world-writable — so other users on the host can't poison
    the readiness signal (spoof "ready" by ``touch``\\ ing the path) or
    set up a symlink attack on a predictable ``/tmp`` filename. This is
    the right home in production (mounted as the kairix data volume) and
    in dev (under the per-user XDG / Application Support tree).

    Because the flag now lives on the same persistent volume as the
    SQLite index, a restarted container will see the previous process's
    flag if it shut down without clearing it. The MCP entry point calls
    :func:`kairix.platform.warm.state.reset_warm_state` at startup so a
    cold container correctly reports not-ready until it has actually
    re-warmed.

    Operators with a layout where ``data_dir`` is read-only can set
    ``KAIRIX_WARM_FLAG_PATH`` to relocate the flag — but it must point
    at a writable, non-world-writable location.
    """
    override = os.environ.get("KAIRIX_WARM_FLAG_PATH", "").strip()
    return Path(override) if override else data_dir() / "warm.flag"


def connector_sync_disabled() -> bool:
    """Return True when ``KAIRIX_CONNECTOR_SYNC_DISABLED`` is set to any truthy value.

    Operators set this to short-circuit the worker's connector sync tick
    without removing the worker dispatch slot — useful on hosts where
    the connector framework is intentionally inert (legacy scanner
    deploys, partial rollouts).

    Accepted truthy values: ``1``, ``true``, ``yes`` (case-insensitive).
    Anything else — including unset — is False. F4-clean: the env read
    lives here at the boundary.
    """
    return os.environ.get("KAIRIX_CONNECTOR_SYNC_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def connect_browser_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when ``kairix connect *`` must NOT open a real browser.

    Hard kill-switch on the ``_DefaultBrowser.open`` fallback in every
    ``kairix.connect.oauth2.*`` flow. Production leaves this unset so
    the operator's normal flow opens the consent screen; the pytest
    session in ``tests/conftest.py`` sets it to ``"1"`` at collection
    time so any test that forgets to inject a ``FakeBrowserLauncher``
    (or any subprocess that escapes the ``_inject`` patching pattern)
    is hard-blocked rather than silently firing a real popup against
    the operator's machine.

    Reason for existence: 2026-06-01 incident — agent test runs leaked
    real ``webbrowser.open`` calls with placeholder OAuth ``client_id``s,
    producing a stream of Slack "client_id not valid" approval popups
    on the operator's desktop. The injection seams in each flow are
    correct; this is defence-in-depth so a single missed seam can't
    repeat the symptom.

    Args:
        env: Optional env mapping for unit tests to drive the parser
            without monkeypatching ``os.environ`` (F2-clean). Production
            callers leave this ``None`` and the live ``os.environ`` is
            read at call time.

    Accepted truthy values: ``1``, ``true``, ``yes`` (case-insensitive).
    Anything else — including unset — is False. F4-clean: the env read
    lives here at the boundary.
    """
    e = env if env is not None else os.environ
    return e.get("KAIRIX_CONNECT_DISABLE_BROWSER", "").strip().lower() in {"1", "true", "yes"}


def worker_writes_vec_index() -> bool:
    """Return True when the embed-loop worker should write to the usearch ANN index.

    Default is **False** because the in-process usearch writer rebuilds the
    full HNSW graph in RAM on first write per cycle (see issue #335 — the
    1.27M-vector rebuild needs ~7.8 GB resident, blowing past any sane
    worker mem_limit). With the default, the worker writes only to
    ``content_vectors`` in SQLite; the usearch on-disk index is brought
    up to date by ``kairix index-rebuild`` run out-of-band (manual or
    scheduled subprocess with appropriate RAM ceiling).

    Operators who run on hosts where the worker can spare 10+ GiB during
    embed cycles can opt in with ``KAIRIX_WORKER_WRITES_VEC_INDEX=1``;
    everywhere else, treat the usearch index as eventually consistent
    against ``content_vectors``.

    Accepted truthy values: ``1``, ``true``, ``yes`` (case-insensitive).
    """
    return os.environ.get("KAIRIX_WORKER_WRITES_VEC_INDEX", "").strip().lower() in {"1", "true", "yes"}


def noninteractive_mode() -> bool:
    """Return True when ``KAIRIX_NONINTERACTIVE=1`` is set in the environment.

    Centralised here so destructive CLI surfaces (``kairix store crawl
    --reset``, future bulk-delete primitives) read one canonical boundary
    instead of each scattering an ``os.environ.get`` (F4). Operators set
    this in pipelines / containers where prompting is impossible and the
    ``--confirm`` interlock would otherwise block automation.

    Accepted truthy values: ``1``, ``true``, ``yes`` (case-insensitive).
    Anything else — including unset — is False.
    """
    raw = os.environ.get("KAIRIX_NONINTERACTIVE", "").strip().lower()
    return raw in {"1", "true", "yes"}


def preflight_strict() -> bool:
    """Return True when ``KAIRIX_PREFLIGHT_STRICT=1`` makes preflight gaps fatal.

    The worker calls :func:`kairix.core.db.integrity.check_integrity`
    on boot and, by default, logs error-severity gaps but keeps
    running so a slightly-degraded VM doesn't crashloop. Operators
    who want a hard stop on degraded boot set this env var to ``1``
    (typical for staging / canary deploys); production VMs leave it
    unset.

    Accepted truthy values: ``1``, ``true``, ``yes`` (case-insensitive).
    Anything else — including unset — is False. F4-clean: the env
    read lives here at the boundary, the worker passes the boolean
    through.
    """
    raw = os.environ.get("KAIRIX_PREFLIGHT_STRICT", "").strip().lower()
    return raw in {"1", "true", "yes"}


def entity_overrides_path(*, document_root_arg: str | Path | None = None) -> Path:
    """Path to the operator-edited entity overrides file.

    Default: ``{document_root}/04-Agent-Knowledge/_entity-overrides.md``.

    Operators add terms the NER model misses or mistypes (e.g. company
    acronyms, project codenames) so ``kairix entity suggest`` picks them
    up. The file format is documented in
    ``docs/user-guide/entity-overrides.md`` — closes #166.

    Override via ``KAIRIX_ENTITY_OVERRIDES_PATH`` for tests and custom
    deployments. The env read stays in this module (F4).

    ``document_root_arg`` lets callers (e.g. the store CLI) pin the path
    against a per-invocation document root that does not necessarily
    match the cached default. When supplied, the env-var override still
    wins so operators retain the documented escape hatch.
    """
    raw = os.environ.get("KAIRIX_ENTITY_OVERRIDES_PATH")
    if raw:
        return Path(raw).expanduser()
    base = Path(document_root_arg) if document_root_arg is not None else document_root()
    return base / _AGENT_KNOWLEDGE_DIR / "_entity-overrides.md"


def feature_flag_override(name: str) -> bool | None:
    """Read the ``KAIRIX_FEATURE_<UPPERCASE>`` env-var override for a flag.

    Returns ``True`` / ``False`` when the env var is set to a recognised
    truthy / falsy value; returns ``None`` when the var is unset (so the
    resolver falls through to the config-overlay layer).

    Accepted truthy values: ``1``, ``true``, ``yes``, ``on`` (case-
    insensitive). Accepted falsy values: ``0``, ``false``, ``no``,
    ``off``. Anything else logs a warning and returns ``None`` so the
    resolver treats the override as absent.

    Lives in :mod:`kairix.paths` per F4 — every ``KAIRIX_*`` env read
    stays at the paths boundary. See
    ``docs/architecture/feature-flag-architecture.md`` §3.4.
    """
    env_name = f"KAIRIX_FEATURE_{name.upper()}"
    raw = os.environ.get(env_name)
    if raw is None:
        return None
    normalised = raw.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "%s=%r is not a recognised boolean; ignoring override",
        env_name,
        raw,
    )
    return None


def feature_flag_config_overlay() -> dict[str, bool]:
    """Return the ``features:`` section from ``kairix.config.yaml``.

    Returns an empty dict when the section is absent or the config file
    does not exist. Non-bool values are coerced through Python's
    truthiness so operators who write ``features: {foo: 1}`` get the
    intuitive interpretation; explicit ``True`` / ``False`` are
    preserved untouched.

    F4 boundary: keeping the yaml read here means the resolver does not
    grow its own ``os.environ`` / file-system read surface. See
    ``docs/architecture/feature-flag-architecture.md`` §3.4.
    """
    config_path = os.environ.get(_KAIRIX_CONFIG_PATH_ENV) or "kairix.config.yaml"
    try:
        import yaml

        p = Path(config_path).expanduser()
        if not p.exists():
            return {}
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        section = data.get("features") or {}
        if not isinstance(section, dict):
            return {}
        return {str(k): bool(v) for k, v in section.items()}
    except (OSError, ImportError, AttributeError):
        # Graceful fallback when the config file is missing, yaml is
        # unavailable, or yaml.safe_load returns an unexpected shape.
        return {}


def agent_knowledge_dir_name(*, config: dict[str, Any] | None = None) -> str:
    """Return the agent-knowledge directory name under ``document_root``.

    Default ``"04-Agent-Knowledge"``. Override via ``paths.agent_knowledge_dir``
    in ``kairix.config.yaml`` for vaults that name the tree differently.

    ``config`` is the test seam — production callers leave it ``None`` and
    the helper reads ``kairix.config.yaml`` itself.
    """
    paths_cfg = load_paths_from_config() if config is None else config
    return str(paths_cfg.get("agent_knowledge_dir") or _AGENT_KNOWLEDGE_DIR)


def agent_memory_glob(*, config: dict[str, Any] | None = None) -> str:
    """Glob (relative to the agent-knowledge dir) for memory log files.

    Default ``"**/*.md"`` — any markdown anywhere under the agent-knowledge
    tree counts as a memory log. Operators with a stricter layout pin the
    pattern via ``paths.agent_memory_glob`` in ``kairix.config.yaml``
    (e.g. ``"*/memory/*.md"`` to require ``<agent>/memory/<file>.md``).

    ``config`` is the test seam.
    """
    paths_cfg = load_paths_from_config() if config is None else config
    return str(paths_cfg.get("agent_memory_glob") or "**/*.md")


def agent_memory_path(agent: str, *, root: Path | str | None = None) -> Path:
    """Get the memory directory for an agent.

    Default: {document_root}/04-Agent-Knowledge/{agent}/memory
    Override via the ``root`` kwarg, or via ``KAIRIX_AGENT_MEMORY_ROOT``
    env var for custom layouts.

    If the override path already ends with /{agent}/memory (a common
    misuse — passing the full agent-memory path rather than the parent
    of agent directories), the function detects this and returns the
    path as-is rather than double-appending. Silently handling the
    misuse with a warning is friendlier than failing.

    ``root`` is the test seam (F2-clean): tests pass an explicit root
    instead of monkeypatching ``KAIRIX_AGENT_MEMORY_ROOT``.
    Production callers leave it None and the env-var path applies.
    """
    if root is None:
        root = os.environ.get("KAIRIX_AGENT_MEMORY_ROOT")
    if root:
        override_path = Path(root)
        if override_path.parts[-2:] == (agent, "memory"):
            logger.warning(
                "agent_memory_path: root override already ends with "
                "/%s/memory; using it as-is to avoid path-doubling. Pass the "
                "parent of the agent directories (e.g. .../04-Agent-Knowledge) "
                "to silence this warning.",
                agent,
            )
            return override_path
        return override_path / agent / "memory"
    return document_root() / _AGENT_KNOWLEDGE_DIR / agent / "memory"
