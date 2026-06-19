"""Linear connector plugin — workspace roadmap + docs via the Linear GraphQL API.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for one Linear workspace. Polls five entity types (issue / project /
document / initiative / projectUpdate) filtered + ordered by
``updatedAt``, renders each to Markdown, and emits one
:class:`~kairix.core.protocols.ChangeEvent` per node with a type-prefixed
``item_id``.

Auth is a single Linear workspace / personal API key. The connector
resolves the key via the injected :class:`~kairix.secrets.SecretsResolver`
(canonical leaf ``("connector", "linear", None, "api_key")``) and threads
it through every API call via
:class:`kairix.connectors.linear.api_client.LinearApiClient`. One API
key = one workspace (no single credential spans workspaces, per spec §1).

All Linear traffic is HTTPS-only (spec §3): the endpoint is a hard-coded
``https://api.linear.app/graphql`` constant and the client rejects any
non-``https`` scheme.

Registered via ``[project.entry-points."kairix.connectors"]`` in kairix's
``pyproject.toml`` — operators select it by listing ``linear`` in their
``connectors[]`` config, behind the ``connector_linear`` feature flag
(introduce stage, default off; see
``docs/architecture/feature-flag-architecture.md`` §3).

Decision record (spec §13): change detection is incremental polling
(``PollConnector``, ``updatedAt`` cursor), NOT webhooks — roadmap + docs
change on a human cadence and polling needs only outbound HTTPS, fitting
hardened deployments. Webhooks remain a clean Phase-2 capability. See
:class:`kairix.connectors.linear.connector.LinearConnector` and
``docs/architecture/connector-scope-topology/connector-design-specs/linear.md``
§13.

Default sensitivity tier is ``internal`` per spec §1 (roadmap/docs are
company-internal). Operators routing client-confidential workspaces
override via the connector config's ``default_sensitivity`` key.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38);
chunking happens downstream in ``kairix/core/connectors/silver.py``.

See ``tests/bdd/features/connector_linear.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.linear.api_client import (
    LINEAR_DEFAULT_RETRY_AFTER_S,
    LINEAR_GRAPHQL_ENDPOINT,
    LinearApiClient,
)
from kairix.connectors.linear.connector import (
    CONNECTOR_LINEAR_FLAG,
    CONNECTOR_NAME,
    DEFAULT_PER_TICK_MAX_ITEMS,
    DEFAULT_SENSITIVITY,
    LINEAR_MARKDOWN_MIME,
    LinearConnector,
    LinearCredentials,
    make_connector,
)
from kairix.connectors.linear.render import ENTITY_KINDS, render

# F56 capability declaration. Linear satisfies the base SourceConnector
# plus PollConnector (per-entity GraphQL queries filtered by updatedAt),
# CredentialsConnector (validate + carry the workspace API key), and
# SlimConnector (id-only enumeration for the prune cycle). EventConnector
# (webhooks), CheckpointedConnector, OAuthConnector, and HierarchyConnector
# are Phase-2 capabilities (spec §1 / §14).
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "CredentialsConnector",
        "SlimConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "CONNECTOR_LINEAR_FLAG",
    "CONNECTOR_NAME",
    "DEFAULT_PER_TICK_MAX_ITEMS",
    "DEFAULT_SENSITIVITY",
    "ENTITY_KINDS",
    "LINEAR_DEFAULT_RETRY_AFTER_S",
    "LINEAR_GRAPHQL_ENDPOINT",
    "LINEAR_MARKDOWN_MIME",
    "LinearApiClient",
    "LinearConnector",
    "LinearCredentials",
    "make_connector",
    "render",
]

# Plugin version (mirrors the per-plugin version convention). MVP / Approach A.
version = "1.0"
