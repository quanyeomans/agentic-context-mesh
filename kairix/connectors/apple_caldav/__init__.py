"""Apple iCloud CalDAV calendar connector plugin.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for one iCloud account's calendars. The connector wraps the CalDAV
``<sync-collection>`` REPORT (RFC 6578), streams events as
:class:`ChangeEvent`s, and persists per-calendar CalDAV sync tokens as
the incremental-sync cursors.

Auth: HTTP Basic against ``caldav.icloud.com`` with the operator's
iCloud Apple ID + an Apple-issued app-specific password. The app
password is the documented Apple surface for CalDAV — see
``kairix/connectors/apple_caldav/README.md`` for the operator
instructions.

Flag-gated by the ``topology_apple_caldav`` feature flag (default
OFF — see :mod:`kairix.core.features.registry`). Until the flag flips
ON the connector retains the legacy single-cursor shape; when ON,
each iCloud calendar becomes its own :class:`Container` with its own
CalDAV sync token.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``apple_caldav`` in their ``connectors[]`` config.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41.

See ``tests/bdd/features/connector_apple_caldav.feature`` for the
behaviour spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.apple_caldav.client import (
    AppleCalDavClient,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.connectors.apple_caldav.connector import (
    AppleCalDavConfig,
    AppleCalDavConnector,
    make_connector,
)

version: str = "0.1.0"

# F56 capability declaration — connector advertises SourceConnector +
# Poll / Checkpointed / Hierarchy capabilities (CredentialsConnector
# shim included for the framework's preflight path).
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "CheckpointedConnector",
        "HierarchyConnector",
        "CredentialsConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "AppleCalDavClient",
    "AppleCalDavConfig",
    "AppleCalDavConnector",
    "CalDavCalendarRef",
    "CalendarEventRecord",
    "CalendarSyncPage",
    "make_connector",
    "version",
]
