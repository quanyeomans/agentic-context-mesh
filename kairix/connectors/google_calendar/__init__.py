"""Google Calendar connector plugin — events.list syncToken-backed.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for one Google Calendar (one ``calendar_id``, defaulting to
``primary``). The connector calls Google's Calendar API v3
``events.list`` endpoint, streams events as :class:`ChangeEvent` items,
and persists the returned ``nextSyncToken`` as the incremental-sync
cursor.

Flag-gated by the ``topology_v2_google_calendar`` feature flag
(introduce stage, default off — see
:mod:`kairix.core.features.registry`). The flag ships OFF because
Google Workspace OAuth credentials are not yet provisioned in
the operator's Key Vault (tracked GH #356); flipping the flag ON without
provisioning the credential is a no-op (the connector's
``access_token`` config key resolves to empty and ``make_connector``
raises with a fix pointer).

Auth uses an OAuth 2.0 access token configured via the operator's
secret-resolution boundary — the connector itself never touches the
token endpoint. Production callers resolve the token via the
the operator's Key Vault KV-backed secret surface; tests inject a stand-in
``GoogleCalendarClient`` via the ``client_factory`` DI seam so no
real network I/O fires.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``google_calendar`` in their ``connectors[]`` config, behind the
``topology_v2_google_calendar`` flag.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41.

See ``tests/bdd/features/connector_google_calendar.feature`` for the
behaviour spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.google_calendar.client import (
    CALENDAR_API_BASE_URL,
    DEFAULT_PAGE_SIZE,
    GoogleCalendarClient,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
    SyncTokenExpiredError,
    iter_pages_delta,
    iter_pages_initial,
)
from kairix.connectors.google_calendar.connector import (
    CONNECTOR_NAME,
    DEFAULT_INITIAL_WINDOW_DAYS_BACK,
    GOOGLE_CALENDAR_MIME,
    GoogleCalendarConfig,
    GoogleCalendarConnector,
    make_connector,
)

# F40 — module-level version. Bumps land in the same commit as
# behavioural changes so cache-keyed re-ingest can replay against the
# updated rendering.
version: str = "0.1.0"

# F56 capability declaration. Google Calendar satisfies the base
# SourceConnector plus PollConnector (syncToken-keyed delta) and
# CheckpointedConnector (the syncToken itself is the checkpoint).
CAPABILITIES: frozenset[str] = frozenset({"SourceConnector", "PollConnector", "CheckpointedConnector"})

__all__ = [
    "CALENDAR_API_BASE_URL",
    "CAPABILITIES",
    "CONNECTOR_NAME",
    "DEFAULT_INITIAL_WINDOW_DAYS_BACK",
    "DEFAULT_PAGE_SIZE",
    "GOOGLE_CALENDAR_MIME",
    "GoogleCalendarClient",
    "GoogleCalendarConfig",
    "GoogleCalendarConnector",
    "GoogleCalendarEventRecord",
    "GoogleCalendarEventsPage",
    "SyncTokenExpiredError",
    "iter_pages_delta",
    "iter_pages_initial",
    "make_connector",
    "version",
]
