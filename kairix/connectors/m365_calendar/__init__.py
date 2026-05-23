"""M365 calendar connector plugin — Microsoft Graph delta-query backed.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for one mailbox's M365 / Outlook calendar. The connector calls Graph's
``/users/<id>/calendar/calendarView/delta`` endpoint, streams events as
:class:`ChangeEvent`s, and persists a Graph ``@odata.deltaLink`` as
the incremental-sync cursor.

Flag-gated by the ``connector_m365_calendar`` feature flag (introduce
stage, default off — see :mod:`kairix.core.features.registry`).

Auth shares its Azure AD app registration with
:mod:`kairix.connectors.m365_email_headers` (KP-2 sibling): one app
registration grants both Calendar.Read AND Mail.Read application
permissions, and the same tenant id / client id / client secret triple
configures both connectors.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``m365_calendar`` in their ``connectors[]`` config.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41.

See ``tests/bdd/features/connector_m365_calendar.feature`` for the
behaviour spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.m365_calendar.connector import (
    M365CalendarConfig,
    M365CalendarConnector,
    make_connector,
)
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)

# F56 capability declaration (Wave B shims duck-type Protocol satisfaction).
CAPABILITIES: frozenset[str] = frozenset(
    {"SourceConnector", "CheckpointedConnector", "CredentialsConnector", "OAuthConnector"}
)

__all__ = [
    "CAPABILITIES",
    "CalendarDeltaPage",
    "CalendarEventRecord",
    "M365CalendarConfig",
    "M365CalendarConnector",
    "M365GraphCalendarClient",
    "make_connector",
]
