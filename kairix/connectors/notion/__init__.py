"""Notion connector plugin — workspace pages via the Notion REST API.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for one Notion workspace. Pulls page envelopes via
``POST /v1/search`` sorted by ``last_edited_time``, renders block
trees to Markdown via ``GET /v1/blocks/{id}/children``, and emits one
:class:`~kairix.core.protocols.ChangeEvent` per visible page.

Auth is a single Notion integration token (``secret_…``). The connector
resolves the token via :func:`kairix.secrets.get_secret` at construction
time and threads it through every API call via
:class:`kairix.connectors.notion.api_client.NotionApiClient`. One
integration token = one workspace (no single credential spans
workspaces per spec §1).

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``notion`` in their ``connectors[]`` config, behind the
``connector_notion`` feature flag (introduce stage, default off; see
``docs/architecture/feature-flag-architecture.md`` §3).

Default sensitivity tier is ``internal`` per spec §1 (no first-class
Notion ACL — the integration's visible set is the permission;
operator declares the tier per teamspace). Operators routing
client-confidential workspaces override via the connector config's
``default_sensitivity`` key.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38);
chunking happens via the Wave F :class:`MarkdownStructuralChunker v2`
(see ``docs/architecture/connector-scope-topology/connector-design-specs/notion.md``
§6 + F55).

See ``tests/bdd/features/connector_notion.feature`` for the
behaviour spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.notion.api_client import (
    DEFAULT_MAX_BLOCK_DEPTH,
    NotionApiClient,
    NotionBlockRef,
    NotionDatabaseRef,
    NotionPageRef,
    NotionSearchPage,
)
from kairix.connectors.notion.connector import (
    CONNECTOR_NAME,
    CONNECTOR_NOTION_FLAG,
    DEFAULT_SENSITIVITY,
    NOTION_MARKDOWN_MIME,
    NotionConnector,
    NotionCredentials,
    cursor_summary_json,
    make_connector,
)

# F56 capability declaration. Notion satisfies the base SourceConnector
# plus PollConnector (page search keyed on last_edited_time), Slim
# (id-only enumeration for prune), Hierarchy (workspace → root →
# page/database tree), and Credentials (integration-token mapping).
# The Wave-E follow-up commits add Event (webhook surface),
# Checkpointed (richer per-batch state if the Notion API surfaces it),
# Resolver (per-page replay), SlimWithPermSync (weak visibility-only),
# and OAuth (public OAuth integration flow).
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "SlimConnector",
        "HierarchyConnector",
        "CredentialsConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "CONNECTOR_NAME",
    "CONNECTOR_NOTION_FLAG",
    "DEFAULT_MAX_BLOCK_DEPTH",
    "DEFAULT_SENSITIVITY",
    "NOTION_MARKDOWN_MIME",
    "NotionApiClient",
    "NotionBlockRef",
    "NotionConnector",
    "NotionCredentials",
    "NotionDatabaseRef",
    "NotionPageRef",
    "NotionSearchPage",
    "cursor_summary_json",
    "make_connector",
]
