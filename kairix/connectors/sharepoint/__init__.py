"""SharePoint connector plugin — document libraries via Microsoft Graph.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for SharePoint document libraries hosted in a Microsoft 365 tenant.
Pulls envelope + binary content for each configured drive, then hands
the bytes off to the kairix extractor registry (markitdown / pptx /
docx / xlsx / ocr) for per-format extraction. Source uri, sensitivity
tier, and last-modified timestamp travel through to every emitted
chunk per F39.

Auth is OAuth2 client-credentials (app-only) — the connector resolves
the tenant / client / secret triple via :func:`kairix.secrets.get_secret`
at construction time, exchanges for a bearer through
:class:`kairix.transport.auth.OAuth2ClientCredsAuth`, then drives the
Graph ``/drives/{drive-id}/root/delta`` query so sync is incremental
between worker ticks. The triple is shared with the M365 email-headers
+ calendar siblings — one Azure AD app registration, three connectors;
the operator grants ``Sites.Read.All`` + ``Files.Read.All`` alongside
the sibling permissions.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``sharepoint`` in their ``connectors[]`` config, behind the
``connector_sharepoint`` feature flag (introduce stage, default off;
see ``docs/architecture/feature-flag-architecture.md`` §3).

Default sensitivity tier is ``internal`` per ADR-005. Operators routing
client-confidential or personal-tier libraries override via the
connector config's ``default_sensitivity`` key.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38).

See ``tests/bdd/features/connector_sharepoint.feature`` for the
behaviour spec.
"""

from __future__ import annotations

from kairix.connectors.sharepoint.connector import (
    DEFAULT_SENSITIVITY,
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    make_connector,
)
from kairix.connectors.sharepoint.graph_client import (
    DriveDeltaPage,
    DriveItemRef,
    DriveRef,
    SharePointGraphClient,
    SiteRef,
)

# F56 capability declaration — SharePoint satisfies the base
# SourceConnector plus CheckpointedConnector (Graph delta tokens are
# the canonical Onyx CheckpointedConnector pattern), CredentialsConnector
# (client-credentials triple loads as a plain mapping), OAuthConnector
# (the classmethods raise to signal "client-credentials only" for any
# framework path that tries to route through the three-legged flow),
# plus the Wave E capabilities — PollConnector (per-container delta
# query via ``list_changes_for_container``), SlimConnector (id-only
# enumeration for prune), Resolver (per-item failure replay), and
# HierarchyConnector (Site / Drive node tree parent-before-child).
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "CheckpointedConnector",
        "CredentialsConnector",
        "OAuthConnector",
        "PollConnector",
        "SlimConnector",
        "Resolver",
        "HierarchyConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "DEFAULT_SENSITIVITY",
    "DriveDeltaPage",
    "DriveItemRef",
    "DriveRef",
    "SharePointConnector",
    "SharePointCredentials",
    "SharePointDriveSpec",
    "SharePointGraphClient",
    "SiteRef",
    "make_connector",
]
