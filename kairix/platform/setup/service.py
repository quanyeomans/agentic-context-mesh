"""SetupService — the contract between the web setup wizard and its backend.

The flag-gated web wizard (``kairix.platform.setup.web``) renders
screens against this Protocol only; it never talks to providers,
connectors, or the index directly. The production implementation
lives in :mod:`kairix.platform.setup.backends` and is constructed
through :func:`build_setup_service`.

Frozen dataclasses per F42 — every Protocol method returns a value
object, never ``dict[str, Any]``.

Tests drive the wizard with ``FakeSetupService`` from ``tests/fakes.py``
via the ``setup_service_factory`` seam on
:func:`kairix.agents.mcp.transport.build_mcp_app`. Tests of the real
backend construct through :func:`build_setup_service` with fakes
injected at the seams below the service (``SetupServiceDeps``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from kairix.paths import KairixPaths
    from kairix.platform.setup.backends import SetupServiceDeps


@dataclass(frozen=True)
class SetupStatus:
    """Which wizard steps have been completed so far."""

    provider_done: bool
    source_done: bool
    index_done: bool


@dataclass(frozen=True)
class ProviderValidation:
    """Outcome of validating an operator-supplied provider credential.

    ``deployment_missing`` distinguishes Azure's "the key authenticated
    but no deployment carries the probed name" failure (#484) from a
    genuinely bad key — the wizard renders deployment guidance instead
    of key-blame for that case.
    """

    ok: bool
    models: tuple[str, ...]
    error: str | None
    deployment_missing: bool = False


@dataclass(frozen=True)
class FolderScan:
    """Outcome of scanning a candidate source folder before indexing.

    ``words_estimate`` is sample-based: the scan reads at most 200 files
    and extrapolates the per-file average across the full count, and file
    counting stops at 50,000 so a mistyped path can't hang the wizard.
    ``cost_estimate_usd`` prices the extrapolated tokens at the embed
    model's list price — an estimate for the operator, not a bill.
    """

    ok: bool
    files: int
    words_estimate: int
    cost_estimate_usd: float
    error: str | None


@dataclass(frozen=True)
class SourceHint:
    """Container-aware pre-fill for the wizard's folder step (#486).

    ``in_container`` is True when kairix runs inside a container, where
    only mounted folders are visible; ``suggested_path`` carries the
    configured document root (stock compose: ``/data/documents``) so the
    operator starts from the folder they actually mounted. On bare-metal
    installs both read falsy and the folder field starts blank.
    """

    in_container: bool
    suggested_path: str


@dataclass(frozen=True)
class IndexStatus:
    """Progress snapshot for the first-index run."""

    running: bool
    done: bool
    chunks_done: int
    chunks_total: int
    error: str | None


@dataclass(frozen=True)
class SearchPreviewHit:
    """One result row in the first-search preview."""

    title: str
    snippet: str
    source: str
    score: float


@dataclass(frozen=True)
class SearchPreview:
    """Results of the wizard's first-search step."""

    results: tuple[SearchPreviewHit, ...]


@dataclass(frozen=True)
class ConnectSnippet:
    """A copy-paste config block for one agent client."""

    client: str
    config_text: str


@dataclass(frozen=True)
class AgentConnectInfo:
    """Everything an operator needs to point an agent at this kairix."""

    mcp_url: str
    snippets: tuple[ConnectSnippet, ...]


@dataclass(frozen=True)
class HandshakeResult:
    """Outcome of verifying an MCP handshake against the running server."""

    ok: bool
    tools_count: int
    error: str | None


class SetupService(Protocol):
    """Boundary Protocol the web wizard composes against.

    The wizard owns rendering; this service owns every side effect
    (credential validation, secret persistence, folder scanning,
    index runs, search, handshake verification).
    """

    def status(self) -> SetupStatus:
        """Return which wizard steps are complete."""

    def validate_provider(
        self,
        provider: str,
        api_key: str,
        endpoint: str | None,
        deployment: str | None = None,
    ) -> ProviderValidation:
        """Check a credential against the provider; list available models.

        ``deployment`` (#484) is the operator's Azure deployment name —
        when provided it is probed instead of the per-plugin default
        model, because Azure routes requests by deployment name.
        """

    def save_provider(
        self,
        provider: str,
        api_key: str,
        endpoint: str | None,
        model: str | None,
        deployment: str | None = None,
    ) -> None:
        """Persist the validated provider selection + credential.

        ``deployment`` (#484) backstops ``model``: when no model was
        chosen, the Azure deployment name is persisted as the embed
        model so indexing talks to the deployment that validated.
        """

    def scan_folder(self, path: str) -> FolderScan:
        """Scan a candidate folder and estimate indexing size + cost."""

    def source_hint(self) -> SourceHint:
        """Container-aware pre-fill for the folder step (#486)."""

    def save_source(self, path: str) -> None:
        """Persist the chosen folder as the first source."""

    def start_index(self) -> None:
        """Kick off the first index run in the background."""

    def index_status(self) -> IndexStatus:
        """Return the current first-index progress snapshot."""

    def first_search(self, query: str) -> SearchPreview:
        """Run a search against the freshly built index."""

    def agent_connect_info(self) -> AgentConnectInfo:
        """Return the MCP URL + per-client connect snippets."""

    def verify_agent_handshake(self) -> HandshakeResult:
        """Probe the running MCP server and count the tools it offers."""


def build_setup_service(
    *,
    paths: KairixPaths | None = None,
    deps: SetupServiceDeps | None = None,
) -> SetupService:
    """Production factory for the wizard backend.

    Args:
        paths: Optional :class:`kairix.paths.KairixPaths` pinning the
            index database (and therefore the embed lockfile and the
            search pipeline) to an explicit location. ``None`` —
            the production default — resolves through the platform
            chain (env vars, config file, platform defaults).
        deps: Optional :class:`kairix.platform.setup.backends.SetupServiceDeps`
            carrying the injectable collaborators. ``None`` wires the
            real implementations; tests pass fakes for the seams a
            scenario drives.
    """
    from kairix.platform.setup.backends import KairixSetupService

    return KairixSetupService(paths=paths, deps=deps)


__all__ = [
    "AgentConnectInfo",
    "ConnectSnippet",
    "FolderScan",
    "HandshakeResult",
    "IndexStatus",
    "ProviderValidation",
    "SearchPreview",
    "SearchPreviewHit",
    "SetupService",
    "SetupStatus",
    "SourceHint",
    "build_setup_service",
]
