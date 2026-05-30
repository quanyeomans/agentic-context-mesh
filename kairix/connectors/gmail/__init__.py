"""Gmail connector plugin — full-message body + envelope via the Gmail REST API.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for Google Workspace / personal Gmail inboxes. Pulls each Gmail message
as one document — body bytes plus envelope headers (Subject / From /
To / Cc / Bcc / Date / Thread / Labels) — via the Gmail REST surface
(``users.history.list`` for change detection, ``users.messages.get``
for fetch).

Per the Onyx Gmail design pattern: a single connector extracts BOTH the
body and the headers. No separate body / headers flags — the message is
one document with the envelope on :class:`SourceMetadata` and the body
bytes on :class:`RawArtefact`. Attachments surface as metadata only
(filename / size / mime); attachment bodies are out of scope for v1
(the Drive connector is the right home for those).

Auth is OAuth2 (the operator / authorised user grants the
``gmail.readonly`` scope once; the worker exchanges the refresh token
for an access token at tick time). Tracked under GH #356 for the
Workspace OAuth provisioning into ``kv-tc-agents``.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing ``gmail``
in their ``connectors[]`` config, behind the ``connector_gmail``
feature flag (introduce stage, default off; see
``docs/architecture/feature-flag-architecture.md`` §3).

Sensitivity tier defaults to ``client-confidential`` per the Gmail
spec brief (email is more sensitive than docs by default); operators
can override via the connector's ``sensitivity`` config key when their
mailbox content is explicitly low-sensitivity.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38).

See ``tests/bdd/features/connector_gmail.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.gmail.client import (
    GmailAttachment,
    GmailClient,
    GmailHeader,
    GmailMessage,
    GmailStatsSnapshot,
    HistoryPage,
)
from kairix.connectors.gmail.connector import (
    DEFAULT_SENSITIVITY,
    TOPOLOGY_V2_GMAIL_FLAG,
    GmailConnector,
    GmailCredentials,
    make_connector,
)

# Plugin version — surfaced for diagnostic + paydown tooling.
version: str = "0.1.0"

# F56 capability declaration. The connector satisfies SourceConnector
# (base) plus PollConnector + CheckpointedConnector per the Wave-E
# capability mix-in surface.
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "CheckpointedConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "DEFAULT_SENSITIVITY",
    "TOPOLOGY_V2_GMAIL_FLAG",
    "GmailAttachment",
    "GmailClient",
    "GmailConnector",
    "GmailCredentials",
    "GmailHeader",
    "GmailMessage",
    "GmailStatsSnapshot",
    "HistoryPage",
    "make_connector",
    "version",
]
