"""Dex CRM connector plugin — Person/Org entity signals from the Dex API.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for the Dex CRM (https://getdex.com) API. Polls
``api.prod.getdex.com/v1`` on a cursor and emits ``ChangeEvent`` records
for changed contacts (Person), organisations (Org), and relationships
(graph edges). The downstream Silver processor lifts those into
:class:`~kairix.core.protocols.EntitySignal` rows staged in SQLite per
ADR-018 — direct-to-Neo4j writes from this connector are rejected.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` (key ``dex_crm``) — operators select it by
listing ``dex_crm`` in their ``connectors[]`` config. The connector is
flag-gated at introduce stage via the ``connector_dex_crm`` registry
entry (default off) so a misconfigured deploy never silently starts
polling the Dex API.

Auth: a single static API key in ``Authorization: Bearer <key>`` via
:class:`kairix.transport.auth.api_key.ApiKeyAuth`. The secret name is
``connector-dex-api-key`` and resolves via the canonical
:func:`kairix.secrets.get_secret` chain. When the secret is absent the
connector still constructs OK; :meth:`list_changes` raises
:class:`~kairix.transport.auth.api_key.MissingCredentialsError` with a
``fix:`` message so the operator sees an actionable error instead of a
stack trace.

Per F35 / F41, this module only imports from itself plus
``kairix.core.*`` (Protocol surface) and ``kairix.transport.auth.*``
(reusable auth helper). No reach into other connectors. The plugin
carries ``py.typed`` per F41.

See ``tests/bdd/features/connector_dex_crm.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.dex_crm.connector import DexCrmConnector, make_connector

# F56 capability declaration (Wave B shims duck-type Protocol satisfaction).
CAPABILITIES: frozenset[str] = frozenset({"SourceConnector", "PollConnector", "CredentialsConnector"})

__all__ = [
    "CAPABILITIES",
    "DexCrmConnector",
    "make_connector",
]
