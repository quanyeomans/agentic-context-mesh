"""SetupService — the contract between the web setup wizard and its backend.

The flag-gated web wizard (``kairix.platform.setup.web``) renders
screens against this Protocol only; it never talks to providers,
connectors, or the index directly. The production implementation
(:func:`build_setup_service`) is delivered separately — until it
lands, the stub below raises a structured error so a flag-ON
deployment without the backend fails honestly at first request
instead of half-working.

Frozen dataclasses per F42 — every Protocol method returns a value
object, never ``dict[str, Any]``.

Tests drive the wizard with ``FakeSetupService`` from ``tests/fakes.py``
via the ``setup_service_factory`` seam on
:func:`kairix.agents.mcp.transport.build_mcp_app`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SetupStatus:
    """Which wizard steps have been completed so far."""

    provider_done: bool
    source_done: bool
    index_done: bool


@dataclass(frozen=True)
class ProviderValidation:
    """Outcome of validating an operator-supplied provider credential."""

    ok: bool
    models: tuple[str, ...]
    error: str | None


@dataclass(frozen=True)
class FolderScan:
    """Outcome of scanning a candidate source folder before indexing."""

    ok: bool
    files: int
    words_estimate: int
    cost_estimate_usd: float
    error: str | None


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

    def validate_provider(self, provider: str, api_key: str, endpoint: str | None) -> ProviderValidation:
        """Check a credential against the provider; list available models."""

    def save_provider(self, provider: str, api_key: str, endpoint: str | None, model: str | None) -> None:
        """Persist the validated provider selection + credential."""

    def scan_folder(self, path: str) -> FolderScan:
        """Scan a candidate folder and estimate indexing size + cost."""

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


def build_setup_service() -> SetupService:
    """Production factory for the wizard backend.

    The backend implementation ships separately from the wizard UI.
    Until it lands, a flag-ON deployment reaching this stub gets a
    structured failure instead of a half-working wizard.
    """
    raise NotImplementedError(
        "Setup wizard backend is not available in this build. "
        "fix: this build predates the wizard backend; flip setup_wizard_web off. "
        "next: kairix features status"
    )


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
    "build_setup_service",
]
