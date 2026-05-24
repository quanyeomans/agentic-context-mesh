"""Slack connector plugin — Web API + Socket Mode bridge for Slack workspaces.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for Slack workspaces. Pulls channel history via the Web API delta
surface (``conversations.history``) and the realtime push surface
(Socket Mode WebSocket / Events API HTTP callback). Per slack.md §1,
DMs and private channels are honoured with the right F39 sensitivity
tier so engagement-wide retrieval doesn't surface private
correspondence.

Auth is the workspace bot token (``xoxb-…``) plus optional Socket Mode
app token (``xapp-…``). Operators install the app via OAuth v2 (the
contrast with SharePoint, which is app-only) — see
:meth:`kairix.connectors.slack.SlackConnector.oauth_authorization_url`.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing ``slack``
in their ``connectors[]`` config, behind the ``connector_slack``
feature flag (introduce stage, default off; see
``docs/architecture/feature-flag-architecture.md`` §3).

F-rule discipline:

  * F37 — ``slack_sdk`` (incl. ``slack_sdk.socket_mode``) imports stay
    in this plugin tree.
  * F39 — every chunk write carries the channel-derived sensitivity
    tier per slack.md §1.
  * F41 — mypy-strict-clean; carries ``py.typed``.
  * F56 — capability declaration in :data:`CAPABILITIES` (base + Poll +
    Checkpointed + Event satisfies the F56 floor).
  * F58 — :meth:`SlackConnector.load_hierarchy` emits parent-before-child.

See ``tests/bdd/features/connector_slack.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.slack.connector import (
    CONNECTOR_SLACK_FLAG,
    SlackConnector,
    SlackCredentials,
    make_connector,
)
from kairix.connectors.slack.socket_mode import (
    SlackSocketModeHandler,
    SocketModeEvent,
    SocketModeState,
    SocketModeTransport,
)
from kairix.connectors.slack.web_client import (
    PerMethodTokenBucket,
    SlackChannel,
    SlackMessage,
    SlackWebClient,
)

# F56 capability declaration. The connector satisfies every protocol
# listed in slack.md §7; the frozenset lets the AST detector agree with
# the runtime isinstance probe even when optional deps aren't installed.
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "CheckpointedConnector",
        "EventConnector",
        "SlimConnector",
        "SlimConnectorWithPermSync",
        "Resolver",
        "HierarchyConnector",
        "OAuthConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "CONNECTOR_SLACK_FLAG",
    "PerMethodTokenBucket",
    "SlackChannel",
    "SlackConnector",
    "SlackCredentials",
    "SlackMessage",
    "SlackSocketModeHandler",
    "SlackWebClient",
    "SocketModeEvent",
    "SocketModeState",
    "SocketModeTransport",
    "make_connector",
]
