"""M365 email-headers connector plugin — header-only metadata via Microsoft Graph.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for Microsoft 365 / Exchange Online inboxes. Pulls **only** the
From / To / CC / Subject / Date envelope per ADR-004 (Email — Headers
Only); body content is never fetched. Header-only retrieval is enforced
at the Graph query layer through a ``$select`` projection that excludes
every body field — see :mod:`kairix.connectors.m365_email_headers.graph_client`.

Auth is OAuth2 client-credentials (app-only) — the connector resolves
the tenant / client / secret triple via
:class:`kairix.secrets.loader.SecretsLoader` (canonical identity
``(connector, m365, None, <leaf>)``) at construction time, exchanges
for a bearer through
:class:`kairix.transport.auth.OAuth2ClientCredsAuth`, then drives the
Graph ``/users/{upn}/messages/delta`` delta query so sync is
incremental between worker ticks.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``m365_email_headers`` in their ``connectors[]`` config, behind the
``connector_m365_email_headers`` feature flag (introduce stage, default
off; see ``docs/architecture/feature-flag-architecture.md`` §3).

Sensitivity tier is ``personal`` per ADR-004 + ADR-005 — every
:class:`ChangeEvent` and resulting :class:`Chunk` carries the personal
tier on the connector boundary.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38).

See ``tests/bdd/features/connector_m365_email_headers.feature`` for the
behaviour spec.
"""

from __future__ import annotations

from kairix.connectors.m365_email_headers.connector import (
    M365EmailHeadersConnector,
    make_connector,
)
from kairix.connectors.m365_email_headers.graph_client import (
    HEADER_ONLY_SELECT,
    GraphMessage,
    M365GraphClient,
)

# F56 capability declaration (Wave B shims duck-type Protocol satisfaction).
CAPABILITIES: frozenset[str] = frozenset(
    {"SourceConnector", "CheckpointedConnector", "CredentialsConnector", "OAuthConnector"}
)

__all__ = [
    "CAPABILITIES",
    "HEADER_ONLY_SELECT",
    "GraphMessage",
    "M365EmailHeadersConnector",
    "M365GraphClient",
    "make_connector",
]
