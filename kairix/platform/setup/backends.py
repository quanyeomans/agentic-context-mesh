"""Production backends for the web setup wizard's :class:`SetupService`.

:mod:`kairix.platform.setup.service` owns the contract (Protocol + frozen
DTOs); this module owns the side effects behind it. Every collaborator is
injected through :class:`SetupServiceDeps` — production callers construct
through :func:`kairix.platform.setup.service.build_setup_service` with no
arguments and the ``default_factory`` fields wire the real implementations
(lazy-imported at call time). Tests pass fakes at the seams BELOW the
service (fake provider, recorder persistence, tmp-path config files,
scripted index counters) so every branch is reachable without
monkey-patching (F1/F2-clean by construction).

Reused building blocks (one implementation per concern, per #474):

- credential persistence — :func:`kairix.platform.setup.wizard.persist_llm_credentials`
- config writing — :func:`kairix.platform.setup.wizard.write_config_yaml`
- azure plugin split — :func:`kairix.platform.setup.wizard.provider_plugin_name`
- credential probe — :func:`kairix.secrets.probe.llm_credentials_available`
- index run — :func:`kairix.core.embed.use_cases.run_incremental_embed_pipeline`
- index lock — the ``embed.lock`` flock next to the SQLite index, the same
  file :func:`kairix.core.embed.cli.acquire_lock` holds
- search — :func:`kairix.core.factory.build_search_pipeline` (F47 composition)
- handshake — :func:`kairix.agents.mcp.capability_probe.build_capability_probe`

F15: the operator's API key is never logged, and every error string built
from a provider exception is scrubbed of the key before it leaves this
module.
"""

from __future__ import annotations

import fcntl
import importlib.metadata
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.credentials import Credentials
from kairix.platform.setup.service import (
    AgentConnectInfo,
    ConnectSnippet,
    FolderScan,
    HandshakeResult,
    IndexStatus,
    ProviderValidation,
    SearchPreview,
    SearchPreviewHit,
    SetupStatus,
    SourceHint,
)
from kairix.platform.setup.wizard import provider_plugin_name, write_config_yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

#: File suffixes the folder scan counts as indexable text documents.
SCAN_FILE_SUFFIXES = (".md", ".markdown", ".txt")

#: The scan stops counting at this many files so a mistyped path (e.g. "/")
#: can't hang the wizard. The DTO reports the capped count.
SCAN_MAX_FILES = 50_000

#: Word estimation is sample-based: at most this many files are read and
#: the per-file average is extrapolated across the full count.
SCAN_SAMPLE_FILES = 200

#: Embedding price in USD per 1K tokens. Matches the estimate the embed
#: pipeline itself reports (see ``kairix/core/embed/embed.py``) — the
#: text-embedding-3-large list price. One number, grade-8 plain: every
#: thousand tokens of your documents costs about 1/100th of a cent.
EMBED_COST_USD_PER_1K_TOKENS = 0.00013

#: Rough tokens-per-word ratio for English prose — used only for the
#: pre-index cost estimate, never for billing.
TOKENS_PER_WORD = 1.3

#: How many hits the first-search preview shows.
FIRST_SEARCH_TOP_N = 5

#: The two azure plugin names — referenced by the probe-model map, the
#: endpoint requirement, and the endpoint-shape remap (F17 — one site each).
_PLUGIN_AZURE_FOUNDRY = "azure_foundry"
_PLUGIN_AZURE_LEGACY = "azure_legacy"

#: Fallback probe model when a plugin has no entry in
#: :data:`VALIDATION_PROBE_MODELS`.
DEFAULT_VALIDATION_PROBE_MODEL = "text-embedding-3-large"

#: Probe model per provider plugin for credential validation. The wizard
#: validates BEFORE the operator picks a model, so each plugin gets a
#: widely-available default; a successful authenticated round-trip with it
#: is the proof the key works. The Provider Protocol has no model-listing
#: surface, so the validated probe model is returned as the models tuple.
VALIDATION_PROBE_MODELS: Mapping[str, str] = {
    "anthropic": "claude-3-5-haiku-latest",
    "openai": DEFAULT_VALIDATION_PROBE_MODEL,
    _PLUGIN_AZURE_FOUNDRY: DEFAULT_VALIDATION_PROBE_MODEL,
    _PLUGIN_AZURE_LEGACY: DEFAULT_VALIDATION_PROBE_MODEL,
    "litellm_proxy": DEFAULT_VALIDATION_PROBE_MODEL,
    "ollama": "nomic-embed-text",
}

#: Default endpoint filled in when the operator leaves it blank. Mirrors
#: the terminal wizard's OpenAI-direct default.
DEFAULT_PLUGIN_ENDPOINTS: Mapping[str, str] = {
    "openai": "https://api.openai.com/v1",
}

#: Plugins that cannot be probed without an operator-supplied endpoint.
ENDPOINT_REQUIRED_PLUGINS = (_PLUGIN_AZURE_FOUNDRY, _PLUGIN_AZURE_LEGACY)

#: Replacement marker for any API-key occurrence in an error string (F15).
_REDACTED = "[redacted]"

#: Text embedded in the one-call validation probe.
_VALIDATION_PROBE_TEXT = "kairix setup validation"

#: Azure's error code when the resource has no deployment with the
#: requested name. The openai-compat SDK surfaces it inside the error
#: body, so substring detection on the provider's message is reliable.
_AZURE_DEPLOYMENT_NOT_FOUND_CODE = "DeploymentNotFound"

#: Shared reason prefix for a folder the wizard cannot use (F17 — three
#: rejection sites: container scan, bare-metal scan, save_source).
_FOLDER_NOT_FOUND_PREFIX = "Folder not found or not readable: "

#: Shared F21 tail for validation failures (F17 — one definition site).
_VALIDATION_FIX = (
    " fix: the message above is the provider's own error — it can name the"
    " endpoint, the model, or the network, so your key may be fine."
    " next: correct the failing value and validate again."
)

#: Shared F21 tail for indexing failures.
_INDEX_FIX = (
    " fix: check the provider credentials and endpoint, then retry."
    " next: kairix embed status shows what is still pending."
    " run: kairix embed"
)

#: Shared F21 tail for handshake failures.
_HANDSHAKE_FIX = (
    " fix: start the MCP server with kairix mcp serve and check its log. next: verify the connection again."
)


# ---------------------------------------------------------------------------
# Public building blocks (each independently testable through this surface)
# ---------------------------------------------------------------------------


def provider_from_credentials(
    plugin_name: str,
    credentials: Credentials,
    *,
    entry_points: Callable[..., Any] = importlib.metadata.entry_points,
) -> Any:
    """Construct a provider plugin against EXPLICIT credentials.

    The wizard validates keys the operator has typed but not yet saved, so
    the plugin must be built from the supplied values — never from the
    process environment (F2/F4). Every first-party plugin factory accepts
    a ``credentials_resolver`` seam; this passes a resolver that returns
    ``credentials`` regardless of purpose. Factories without the seam
    (e.g. bedrock, whose credentials ride the boto3 chain) are called
    bare.

    ``entry_points`` mirrors :class:`kairix.providers.EntryPointRegistry`'s
    constructor seam: production leaves the stdlib default; tests pass a
    fake callable.
    """
    import inspect

    from kairix.providers import ENTRY_POINT_GROUP, EntryPointRegistry, ProviderNotRegistered

    eps = list(entry_points(group=ENTRY_POINT_GROUP, name=plugin_name))
    if not eps:
        registry = EntryPointRegistry(entry_points=entry_points)
        raise ProviderNotRegistered(plugin_name, registry.available())
    factory = eps[0].load()
    if "credentials_resolver" in inspect.signature(factory).parameters:
        return factory(credentials_resolver=lambda _purpose: credentials)
    return factory()


def probe_provider_roundtrip(provider: Any) -> None:
    """Make one cheap authenticated call against ``provider``; raise on failure.

    Embed-capable plugins prove the credential with a one-text
    ``embed_batch``; chat-only plugins (Anthropic) raise
    ``EmbedNotSupported`` before any request goes out, and the probe
    falls back to a single tiny ``chat`` call. Any transport/auth failure
    propagates as the plugin's canonical typed error so the caller can
    surface the provider's message verbatim.
    """
    from kairix.providers import EmbedNotSupported

    try:
        vectors = provider.embed_batch([_VALIDATION_PROBE_TEXT])
    except EmbedNotSupported:
        reply = provider.chat(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=5,
        )
        if not reply:
            raise RuntimeError("The provider accepted the request but returned an empty chat reply.") from None
        return
    if not vectors or not vectors[0]:
        raise RuntimeError("The provider accepted the request but returned an empty embedding.")


def update_config_file(target: Path, updates: Mapping[str, Any]) -> Path:
    """Merge ``updates`` into the YAML config at ``target`` and write it back.

    Top-level keys are replaced, except dict values (e.g. ``paths:``)
    which merge key-by-key so writing ``paths.document_root`` preserves an
    existing ``paths.db_path``. The write itself goes through the terminal
    wizard's :func:`write_config_yaml` so both setup surfaces emit one
    file shape.
    """
    import yaml

    existing: dict[str, Any] = {}
    if target.exists():
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded
    for key, value in updates.items():
        current = existing.get(key)
        if isinstance(value, Mapping) and isinstance(current, dict):
            current.update(value)
        else:
            existing[key] = dict(value) if isinstance(value, Mapping) else value
    return write_config_yaml(target, "setup-wizard", existing)


def write_config_updates(
    updates: Mapping[str, Any],
    *,
    overlay_path: str | None,
    config_path: str | None,
) -> Path:
    """Merge ``updates`` into the right config file — overlay-aware (#485).

    When an overlay path is configured (``KAIRIX_CONFIG_OVERLAY_PATH``),
    wizard saves land on the OVERLAY file — the operator's base config
    (read-only-mounted in the stock compose) stays pristine, and the
    layered loader (:func:`kairix.core.search.config_loader.load_config`)
    deep-merges the overlay on top of the base at read time. Parent
    directories are created so a first save on a fresh data volume works.

    Without an overlay, the legacy single-file behaviour applies:
    ``config_path`` (``KAIRIX_CONFIG_PATH``) or ``./kairix.config.yaml``.
    """
    if overlay_path:
        target = Path(overlay_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        return update_config_file(target, updates)
    return update_config_file(Path(config_path or "kairix.config.yaml"), updates)


def configured_document_root(
    *,
    override: str | None,
    config_paths: Mapping[str, str],
) -> Path | None:
    """Resolve the EXPLICITLY configured document root, or ``None``.

    The wizard's "source" step is done only when the operator has chosen
    a folder — an env override (``KAIRIX_DOCUMENT_ROOT``) or a config
    ``paths.document_root`` entry. The platform's silent fallback default
    deliberately does NOT count: an unconfigured install must show the
    folder step as pending.
    """
    if override:
        return Path(override).expanduser()
    configured = config_paths.get("document_root")
    if configured:
        return Path(configured).expanduser()
    return None


def count_index_chunks(db_path: Path) -> tuple[int, int]:
    """Return ``(chunks_embedded, chunks_pending)`` from the index database.

    ``chunks_embedded`` counts ``content_vectors`` rows; ``chunks_pending``
    counts documents still awaiting their first embedding, via the same
    :func:`kairix.core.embed.schema.get_pending_chunks` query ``kairix
    embed status`` uses. A missing or not-yet-initialised database reads
    as ``(0, 0)`` — "nothing indexed yet", which is the truthful first-run
    answer, not an error.

    The connection opens through :func:`kairix.core.db.open_db` (F77 —
    one writer-coordinated open site) and is read-only in practice: this
    function only ever issues SELECTs.
    """
    if not db_path.exists():
        return (0, 0)
    from kairix.core.db import open_db
    from kairix.core.embed.schema import get_pending_chunks

    try:
        db = open_db(db_path)
    except sqlite3.Error:
        return (0, 0)
    try:
        embedded = int(db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0])
        pending = len(get_pending_chunks(db))
    except sqlite3.Error:
        return (0, 0)
    finally:
        db.close()
    return (embedded, pending)


def embed_lock_held(lockfile: Path) -> bool:
    """True when another process currently holds the embed flock.

    Probes the same lockfile :func:`kairix.core.embed.cli.acquire_lock`
    takes (``embed.lock`` beside the SQLite index) with a non-blocking
    ``LOCK_EX`` attempt, releasing immediately on success. Opened in
    append mode so the probe never truncates the holder's recorded pid.
    """
    if not lockfile.exists():
        return False
    with open(lockfile, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def _default_embed_pipeline(**kwargs: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.embed.use_cases import run_incremental_embed_pipeline

    return run_incremental_embed_pipeline(**kwargs)


def run_first_index(*, pipeline_fn: Callable[..., Any] = _default_embed_pipeline) -> None:
    """Run one full embed pass; raise with an F21 affordance on failed chunks.

    Delegates to the canonical incremental embed pipeline (which scans the
    document root, rebuilds FTS, embeds pending chunks, and holds the
    embed lock for the duration). The recall gate is skipped — there is no
    baseline to regress against on a first index. ``pipeline_fn`` carries
    the production default (per F6, a real callable — never ``None``);
    tests pass a recorder returning a scripted result.
    """
    result = pipeline_fn(skip_recall_check=True)
    failed = int(getattr(result, "failed", 0))
    if failed:
        raise RuntimeError(f"{failed} chunks failed to embed.{_INDEX_FIX}")


# ---------------------------------------------------------------------------
# Production default seams (lazy-import delegation — wired by SetupServiceDeps)
# ---------------------------------------------------------------------------


def _default_persist_credentials(
    api_key: str,
    endpoint: str,
    embed_model: str,
) -> Path | None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.platform.setup.wizard import persist_llm_credentials

    return persist_llm_credentials(api_key, endpoint, embed_model)


def _default_credentials_probe() -> bool:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.secrets.probe import llm_credentials_available

    return llm_credentials_available()


def _default_configured_document_root() -> Path | None:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.paths import document_root_override, load_paths_from_config

    return configured_document_root(
        override=document_root_override(),
        config_paths=load_paths_from_config(),
    )


# pragma rationale: lazy-import DI-default delegation — the write-target
# resolution reads KAIRIX_CONFIG_OVERLAY_PATH / KAIRIX_CONFIG_PATH
# through kairix.paths (F4); the testable logic lives in
# write_config_updates.
def _default_write_config(updates: Mapping[str, Any]) -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.paths import config_overlay_path_override, config_path_override

    return write_config_updates(
        updates,
        overlay_path=config_overlay_path_override(),
        config_path=config_path_override(),
    )


def _default_search_pipeline(paths: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.core.factory import build_search_pipeline

    return build_search_pipeline(paths=paths)


def _default_capability_probe() -> Mapping[str, Any]:  # pragma: no cover  # lazy-import DI-default delegation
    from kairix.agents.mcp.capability_probe import build_capability_probe

    return build_capability_probe()()


# pragma rationale: lazy-import DI-default delegation — builds the real
# FastMCP server in-process, which the unit tier must not pay for.
def _default_tools_count() -> int:  # pragma: no cover  # lazy-import DI-default delegation
    import asyncio

    from kairix.agents.mcp.server import build_server

    server = build_server()
    return len(asyncio.run(server.list_tools()))


@dataclass
class SetupServiceDeps:
    """Injectable collaborators for :class:`KairixSetupService`.

    Production callers omit every field — the ``default_factory`` wiring
    lazy-imports the real implementation at call time (the EmbedCliDeps /
    WizardDeps pattern). Tests construct ``SetupServiceDeps(...)`` with
    fakes for exactly the seams a scenario drives.
    """

    # validate_provider — plugin construction from explicit credentials.
    provider_factory: Callable[[str, Credentials], Any] = field(
        default_factory=lambda: provider_from_credentials,
    )
    # save_provider — W1-D's canonical persistence (api_key, endpoint, model).
    persist_credentials_fn: Callable[[str, str, str], Path | None] = field(
        default_factory=lambda: _default_persist_credentials,
    )
    # status — is an LLM credential resolvable through the loader chain?
    credentials_probe: Callable[[], bool] = field(
        default_factory=lambda: _default_credentials_probe,
    )
    # status — the explicitly configured document root, or None.
    configured_document_root_fn: Callable[[], Path | None] = field(
        default_factory=lambda: _default_configured_document_root,
    )
    # save_provider / save_source — merge updates into the runtime config file.
    write_config_fn: Callable[[Mapping[str, Any]], Path] = field(
        default_factory=lambda: _default_write_config,
    )
    # status / index_status — (embedded, pending) chunk counters per db path.
    index_counts_fn: Callable[[Path], tuple[int, int]] = field(
        default_factory=lambda: count_index_chunks,
    )
    # start_index / index_status — is the embed flock held by another process?
    embed_lock_probe_fn: Callable[[Path], bool] = field(
        default_factory=lambda: embed_lock_held,
    )
    # start_index — the actual first-index run (background thread target).
    index_runner_fn: Callable[[], None] = field(
        default_factory=lambda: run_first_index,
    )
    # first_search — factory-built pipeline exposing .search(query).
    search_pipeline_factory: Callable[[Any], Any] = field(
        default_factory=lambda: _default_search_pipeline,
    )
    # verify_agent_handshake — layered capability flags.
    capability_probe_fn: Callable[[], Mapping[str, Any]] = field(
        default_factory=lambda: _default_capability_probe,
    )
    # verify_agent_handshake — registered MCP tool count, in-process.
    tools_count_fn: Callable[[], int] = field(
        default_factory=lambda: _default_tools_count,
    )
    # agent_connect_info — env mapping for KAIRIX_MCP_ENDPOINT resolution.
    # None means os.environ (read inside kairix.paths per F4).
    environ: Mapping[str, str] | None = None


class KairixSetupService:
    """Production :class:`kairix.platform.setup.service.SetupService`.

    Construct through
    :func:`kairix.platform.setup.service.build_setup_service` — the seams
    live on :class:`SetupServiceDeps`; ``paths`` pins the index database
    (and therefore the embed lockfile and the search pipeline) to an
    explicit location, with ``None`` meaning the platform resolution
    chain.
    """

    def __init__(self, *, paths: Any = None, deps: SetupServiceDeps | None = None) -> None:
        self._paths = paths
        self._deps = deps if deps is not None else SetupServiceDeps()
        self._index_state_lock = threading.Lock()
        self._index_thread: threading.Thread | None = None
        self._index_error: str | None = None

    # ------------------------------------------------------------------
    # Internal path resolution
    # ------------------------------------------------------------------

    def _db_path(self) -> Path:
        """Index database path — injected ``paths`` or the platform chain."""
        if self._paths is not None:
            return Path(self._paths.db_path)
        from kairix.paths import db_path

        return db_path()

    def _lockfile(self) -> Path:
        """The embed flock lives beside the SQLite index — the same
        ``embed.lock`` path :func:`kairix.core.embed.cli.acquire_lock` uses."""
        return self._db_path().parent / "embed.lock"

    # ------------------------------------------------------------------
    # SetupService Protocol
    # ------------------------------------------------------------------

    def status(self) -> SetupStatus:
        """Derive step completion from what actually exists on this host."""
        provider_done = bool(self._deps.credentials_probe())
        root = self._deps.configured_document_root_fn()
        source_done = root is not None and root.is_dir()
        embedded, _pending = self._deps.index_counts_fn(self._db_path())
        return SetupStatus(
            provider_done=provider_done,
            source_done=source_done,
            index_done=embedded > 0,
        )

    def validate_provider(
        self,
        provider: str,
        api_key: str,
        endpoint: str | None,
        deployment: str | None = None,
    ) -> ProviderValidation:
        """One authenticated round-trip against the SUPPLIED credentials.

        Nothing is persisted and the process environment is never touched
        (F2/F4) — the plugin is constructed against the typed-in values.
        Failures carry the provider's error verbatim (key scrubbed, F15)
        plus an F21 affordance. The Provider Protocol exposes no model
        listing, so success returns the probe model as the models tuple.

        ``deployment`` (#484): Azure routes requests by deployment name,
        so when the operator supplies one it replaces the per-plugin
        probe-model default. A ``DeploymentNotFound`` failure is
        reported as "your key works, this deployment name doesn't" —
        not as a bad key.
        """
        plugin = _normalise_plugin_name(provider, endpoint)
        resolved_endpoint = (endpoint or "").strip() or DEFAULT_PLUGIN_ENDPOINTS.get(plugin, "")
        if plugin in ENDPOINT_REQUIRED_PLUGINS and not resolved_endpoint:
            return ProviderValidation(
                ok=False,
                models=(),
                error=(
                    f"The {plugin} provider needs an endpoint before the key can be checked."
                    " fix: paste your resource endpoint"
                    " (it looks like https://<resource>.services.ai.azure.com)."
                    " next: validate again."
                ),
            )
        model = (deployment or "").strip() or VALIDATION_PROBE_MODELS.get(plugin, DEFAULT_VALIDATION_PROBE_MODEL)
        credentials = Credentials(api_key=api_key, endpoint=resolved_endpoint, model=model)
        try:
            plugin_obj = self._deps.provider_factory(plugin, credentials)
            probe_provider_roundtrip(plugin_obj)
        except Exception as exc:
            message = _scrub_secret(str(exc), api_key)
            if _AZURE_DEPLOYMENT_NOT_FOUND_CODE in message:
                return ProviderValidation(
                    ok=False,
                    models=(),
                    error=_deployment_missing_error(model),
                    deployment_missing=True,
                )
            return ProviderValidation(ok=False, models=(), error=f"{message}{_VALIDATION_FIX}")
        return ProviderValidation(ok=True, models=(model,), error=None)

    def save_provider(
        self,
        provider: str,
        api_key: str,
        endpoint: str | None,
        model: str | None,
        deployment: str | None = None,
    ) -> None:
        """Persist the validated selection through W1-D's canonical path.

        The credential lands in the operator secrets bundle via
        ``persist_llm_credentials`` (the same upsert ``kairix secrets set``
        uses) and the chosen plugin name lands in the config's
        ``provider:`` field — #474's headline defect was a wizard that
        asked and then didn't write it. When no model was chosen, the
        Azure ``deployment`` name (#484) fills the embed-model slot so
        indexing talks to the same deployment that validated.
        """
        plugin = _normalise_plugin_name(provider, endpoint)
        resolved_endpoint = (endpoint or "").strip() or DEFAULT_PLUGIN_ENDPOINTS.get(plugin, "")
        resolved_model = (model or "").strip() or (deployment or "").strip()
        self._deps.persist_credentials_fn(api_key, resolved_endpoint, resolved_model)
        self._deps.write_config_fn({"provider": plugin})

    def scan_folder(self, path: str) -> FolderScan:
        """Walk ``path`` and estimate indexing size and cost.

        Counts markdown/text files (capped at :data:`SCAN_MAX_FILES`),
        reads at most :data:`SCAN_SAMPLE_FILES` of them to estimate words,
        and prices the extrapolated token count at
        :data:`EMBED_COST_USD_PER_1K_TOKENS`.

        Relative paths are rejected with a message naming the resolution
        base instead of silently joining the server's working directory
        (#486); inside a container, a not-found error points at the
        mounted document root as the candidate to try.
        """
        candidate = (path or "").strip()
        if not candidate:
            return _failed_scan("No folder path was provided.")
        folder = Path(candidate).expanduser()
        if not folder.is_absolute():
            return _failed_scan(
                _relative_path_reason(candidate),
                fix="enter the full path, starting with / (or ~ for your home folder).",
            )
        if not folder.is_dir():
            mounted = self.source_hint().suggested_path
            if mounted:
                return _failed_scan(
                    f"{_FOLDER_NOT_FOUND_PREFIX}{folder}.",
                    fix=(
                        "this kairix runs in Docker, so it only sees folders mounted"
                        f" into the container — the standard compose mounts your documents at {mounted}."
                    ),
                    next_=f"try {mounted}, then scan once more.",
                )
            return _failed_scan(f"{_FOLDER_NOT_FOUND_PREFIX}{folder}.")
        files, words_estimate = _scan_text_files(folder)
        cost = (words_estimate * TOKENS_PER_WORD / 1000.0) * EMBED_COST_USD_PER_1K_TOKENS
        return FolderScan(
            ok=True,
            files=files,
            words_estimate=words_estimate,
            cost_estimate_usd=round(cost, 4),
            error=None,
        )

    def save_source(self, path: str) -> None:
        """Persist the chosen folder as ``paths.document_root`` in the config.

        Raises:
            ValueError: when the path is relative or the folder does not
                exist — the wizard's scan step gates the happy path, so
                reaching here with a bad path is a hard reject, not a
                silent write against the server's working directory.
        """
        candidate = (path or "").strip()
        folder = Path(candidate).expanduser()
        if not folder.is_absolute():
            raise ValueError(
                f"{_relative_path_reason(candidate)}"
                " fix: enter the full path, starting with / (or ~ for your home folder)."
                " next: scan the folder again, then save."
            )
        if not folder.is_dir():
            raise ValueError(
                f"{_FOLDER_NOT_FOUND_PREFIX}{folder}."
                " fix: create the folder (or fix the spelling), then scan it again."
                " next: the scan step confirms the folder before it is saved."
            )
        self._deps.write_config_fn({"paths": {"document_root": str(folder)}})
        # The platform paths resolution is cached per process — drop it so
        # the very next resolve (the index run this wizard kicks off) sees
        # the folder that was just saved.
        from kairix.paths import clear_cache

        clear_cache()

    def source_hint(self) -> SourceHint:
        """Container-aware pre-fill for the folder step (#486).

        Inside a container the only folders kairix can see are the ones
        the operator mounted, so the folder field pre-fills with the
        configured document root (stock compose: ``/data/documents``).
        The env read lives in :func:`kairix.paths.container_source_prefill`
        (F4); ``deps.environ`` is the F2-clean test seam.
        """
        from kairix.paths import container_source_prefill

        prefill = container_source_prefill(self._deps.environ)
        return SourceHint(in_container=prefill is not None, suggested_path=prefill or "")

    def start_index(self) -> None:
        """Kick off the first index run in a background thread.

        Idempotent: a second click while a run is in flight is a no-op.
        When another process (the embed worker, a terminal ``kairix
        embed``) already holds the embed flock, no thread is spawned —
        ``index_status`` reports that run truthfully as the indexer.
        """
        with self._index_state_lock:
            if self._index_thread is not None and self._index_thread.is_alive():
                return
            if self._deps.embed_lock_probe_fn(self._lockfile()):
                return
            self._index_error = None
            thread = threading.Thread(target=self._index_worker, name="setup-wizard-index", daemon=True)
            self._index_thread = thread
            thread.start()

    def _index_worker(self) -> None:
        """Background-thread body — records every failure as an operator message.

        ``SystemExit`` is recorded and then re-raised (Sonar S5754): in a
        worker thread the re-raise is harmless — ``threading`` silently
        swallows ``SystemExit`` from non-main threads — so the interpreter
        keeps running and ``index_status`` reports the recorded message.
        """
        try:
            self._deps.index_runner_fn()
        except SystemExit:  # NOSONAR(python:S5754) — deliberate; see rationale below.
            # acquire_lock exhausts its wait window with sys.exit(3) when a
            # concurrent embed genuinely holds the lock the whole time.
            # Re-raising inside this daemon thread would kill only the
            # thread, not "stop the application" — so we convert it to
            # the operator-facing status the wizard polls instead.
            with self._index_state_lock:
                self._index_error = (
                    "Indexing stopped: another indexing run is already in progress."
                    " fix: wait for it to finish, then start again."
                    " next: kairix worker status shows the active phase."
                )
            raise
        except Exception as exc:
            with self._index_state_lock:
                self._index_error = f"Indexing stopped: {exc}{_INDEX_FIX}"

    def index_status(self) -> IndexStatus:
        """Progress snapshot from the index database's own counters.

        Progress is read from the ``content_vectors`` / pending-documents
        tables rather than a live in-pipeline counter — the embed pipeline
        commits per batch, so the DB count IS the ground truth, and it
        works identically whether this service's thread or the embed
        worker is doing the indexing. ``chunks_total`` is embedded +
        pending documents, so it grows as the scan chunkifies new files.
        """
        with self._index_state_lock:
            thread_alive = self._index_thread is not None and self._index_thread.is_alive()
            error = self._index_error
        embedded, pending = self._deps.index_counts_fn(self._db_path())
        external_run = not thread_alive and self._deps.embed_lock_probe_fn(self._lockfile())
        running = thread_alive or external_run
        done = not running and error is None and pending == 0 and embedded > 0
        return IndexStatus(
            running=running,
            done=done,
            chunks_done=embedded,
            chunks_total=embedded + pending,
            error=error,
        )

    def first_search(self, query: str) -> SearchPreview:
        """Top-5 hits from the factory-built production pipeline.

        Scores are normalised relative to the best hit (top hit = 1.0) so
        the preview's percentage reads as "how close to the best match",
        which is meaningful across fusion strategies. Any pipeline failure
        returns an empty preview — the screen's empty state guides the
        operator; a stack trace does not.
        """
        try:
            pipeline = self._deps.search_pipeline_factory(self._paths)
            result = pipeline.search(query)
            rows = list(getattr(result, "results", ()))
        except Exception:
            logger.warning("setup wizard: first-search pipeline failed", exc_info=True)
            return SearchPreview(results=())
        return SearchPreview(results=_to_preview_hits(rows))

    def agent_connect_info(self) -> AgentConnectInfo:
        """MCP URL + copy-paste snippets, shapes from connecting-agents.md."""
        from kairix.paths import mcp_endpoint

        url = mcp_endpoint(environ=self._deps.environ)
        claude_code = {"mcpServers": {"kairix": {"type": "http", "url": url}}}
        openclaw = {
            "mcp": {
                "servers": {
                    "mcp-kairix": {
                        "command": "kairix",
                        "args": ["mcp", "serve"],
                        "description": "Knowledge base search, research, entity lookup",
                    }
                }
            }
        }
        return AgentConnectInfo(
            mcp_url=url,
            snippets=(
                ConnectSnippet(client="Claude Code (.mcp.json)", config_text=json.dumps(claude_code, indent=2)),
                ConnectSnippet(client="OpenClaw (openclaw.json)", config_text=json.dumps(openclaw, indent=2)),
                ConnectSnippet(client="Generic MCP over HTTP", config_text=url),
            ),
        )

    def verify_agent_handshake(self) -> HandshakeResult:
        """In-process handshake proof: capability probe + registered tool count.

        No HTTP self-call — the wizard runs inside the same process as
        the MCP transport, so the layered capability probe plus the
        actual registered-tool count is the honest signal an agent
        connecting right now would observe.
        """
        try:
            probe = self._deps.capability_probe_fn()
            tools = int(self._deps.tools_count_fn())
        except Exception as exc:
            return HandshakeResult(ok=False, tools_count=0, error=f"MCP handshake check failed: {exc}.{_HANDSHAKE_FIX}")
        if tools <= 0:
            return HandshakeResult(
                ok=False,
                tools_count=0,
                error=f"The MCP server registered no tools.{_HANDSHAKE_FIX}",
            )
        if not probe.get("secrets_loaded", False):
            detail = _probe_detail(probe, "secrets_loaded")
            return HandshakeResult(
                ok=False,
                tools_count=tools,
                error=(
                    f"Agents can connect, but the provider credentials are not loaded: {detail}."
                    " fix: finish the provider step so search returns real results."
                    " next: verify the connection again."
                ),
            )
        return HandshakeResult(ok=True, tools_count=tools, error=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise_plugin_name(provider: str, endpoint: str | None) -> str:
    """Reuse the wizard's azure endpoint-shape split; other names pass through.

    A legacy ``<r>.openai.azure.com`` endpoint must ride ``azure_legacy``
    even when the operator clicked ``azure_foundry`` (and vice versa) —
    :func:`provider_plugin_name` owns that mapping, so it is not
    reimplemented here. Non-azure picks are concrete plugin names from
    the installed registry and pass through verbatim.
    """
    if provider in (_PLUGIN_AZURE_FOUNDRY, _PLUGIN_AZURE_LEGACY) and endpoint:
        return provider_plugin_name("azure", endpoint)
    return provider


def _scrub_secret(text: str, secret: str) -> str:
    """Replace any occurrence of ``secret`` in ``text`` with a marker (F15)."""
    if secret and secret in text:
        return text.replace(secret, _REDACTED)
    return text


#: Default F21 markers for a failed scan; specific failures (relative
#: path, container not-found) override with sharper guidance.
_SCAN_DEFAULT_FIX = "check the folder path exists on this machine and kairix can read it."
_SCAN_DEFAULT_NEXT = "enter the path again, then scan once more."


def _failed_scan(reason: str, *, fix: str = _SCAN_DEFAULT_FIX, next_: str = _SCAN_DEFAULT_NEXT) -> FolderScan:
    """A failed FolderScan with the F21 affordance attached."""
    return FolderScan(
        ok=False,
        files=0,
        words_estimate=0,
        cost_estimate_usd=0.0,
        error=f"{reason} fix: {fix} next: {next_}",
    )


def _relative_path_reason(candidate: str) -> str:
    """Why a relative path is rejected — NAMES the resolution base (#486).

    Silently joining the server's working directory surprises operators
    (the wizard runs server-side, often inside a container, so "here"
    is not the browser's folder).
    """
    return (
        f"That looks like a relative path: {candidate!r}. kairix would resolve it"
        f" against the server's working folder ({Path.cwd()}), which is rarely what you meant."
    )


def _deployment_missing_error(probe_model: str) -> str:
    """Azure DeploymentNotFound guidance (#484) — the key WORKED.

    Azure authenticated the request and then reported that no deployment
    carries the probed name, so key-blame guidance would send the
    operator in the wrong direction.
    """
    return (
        f"Your key works, but this Azure resource has no deployment named '{probe_model}'."
        " fix: enter one of your deployment names in the deployment field —"
        " they are listed in the Azure portal under your resource's Deployments page."
        " next: validate again."
    )


def _iter_text_files(folder: Path) -> Iterator[Path]:
    """Yield indexable files under ``folder``, stopping at :data:`SCAN_MAX_FILES`."""
    yielded = 0
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            if not name.lower().endswith(SCAN_FILE_SUFFIXES):
                continue
            yield Path(dirpath) / name
            yielded += 1
            if yielded >= SCAN_MAX_FILES:
                return


def _sample_words(candidate: Path) -> int | None:
    """Word count of one file, or ``None`` when it cannot be read."""
    try:
        return len(candidate.read_text(encoding="utf-8", errors="replace").split())
    except OSError:
        logger.debug("setup wizard: scan could not read %s", candidate)
        return None


def _scan_text_files(folder: Path) -> tuple[int, int]:
    """Count indexable files under ``folder`` and estimate their total words.

    Returns ``(files, words_estimate)``. Counting stops at
    :data:`SCAN_MAX_FILES`; words are read from at most
    :data:`SCAN_SAMPLE_FILES` files and the average is extrapolated.
    Unreadable files (broken symlinks, permission holes) are skipped from
    the word sample but still counted as files.
    """
    files = 0
    sampled = 0
    sampled_words = 0
    for candidate in _iter_text_files(folder):
        files += 1
        if sampled >= SCAN_SAMPLE_FILES:
            continue
        words = _sample_words(candidate)
        if words is not None:
            sampled += 1
            sampled_words += words
    if sampled == 0:
        return (files, 0)
    average = sampled_words / sampled
    return (files, int(average * files))


def _to_preview_hits(rows: Sequence[Any]) -> tuple[SearchPreviewHit, ...]:
    """Map pipeline ``BudgetedResult`` rows to preview hits, top-N, scores
    normalised against the best hit so the top result reads 100%."""
    top = list(rows)[:FIRST_SEARCH_TOP_N]
    raw_scores = [_row_score(row) for row in top]
    best = max(raw_scores, default=0.0)
    hits = []
    for row, raw in zip(top, raw_scores, strict=True):
        fused = getattr(row, "result", row)
        snippet = getattr(row, "content", "") or getattr(fused, "snippet", "")
        hits.append(
            SearchPreviewHit(
                title=str(getattr(fused, "title", "") or getattr(fused, "path", "")),
                snippet=str(snippet),
                source=str(getattr(fused, "path", "")),
                score=(raw / best) if best > 0 else 0.0,
            )
        )
    return tuple(hits)


def _row_score(row: Any) -> float:
    """Best available relevance signal on a pipeline result row."""
    fused = getattr(row, "result", row)
    boosted = float(getattr(fused, "boosted_score", 0.0) or 0.0)
    if boosted:
        return boosted
    return float(getattr(fused, "rrf_score", 0.0) or 0.0)


def _probe_detail(probe: Mapping[str, Any], key: str) -> str:
    """Human detail string for a failed capability, with a safe default."""
    detail = probe.get("detail")
    if isinstance(detail, Mapping):
        text = detail.get(key)
        if text:
            return str(text)
    return "no detail reported"


__all__ = [
    "DEFAULT_PLUGIN_ENDPOINTS",
    "DEFAULT_VALIDATION_PROBE_MODEL",
    "EMBED_COST_USD_PER_1K_TOKENS",
    "ENDPOINT_REQUIRED_PLUGINS",
    "FIRST_SEARCH_TOP_N",
    "SCAN_FILE_SUFFIXES",
    "SCAN_MAX_FILES",
    "SCAN_SAMPLE_FILES",
    "TOKENS_PER_WORD",
    "VALIDATION_PROBE_MODELS",
    "KairixSetupService",
    "SetupServiceDeps",
    "configured_document_root",
    "count_index_chunks",
    "embed_lock_held",
    "probe_provider_roundtrip",
    "provider_from_credentials",
    "run_first_index",
    "update_config_file",
    "write_config_updates",
]
