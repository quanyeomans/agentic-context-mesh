"""GitHub connector plugin — repos / issues / PRs via REST + GraphQL.

First-party :class:`kairix.core.protocols.SourceConnector` plus full
Wave-E capability mix-in implementation for GitHub. Built greenfield
per the canonical design spec at
``docs/architecture/connector-scope-topology/connector-design-specs/github.md``.

The plugin advertises every capability the spec §1 declares:

* ``SourceConnector`` (base)
* ``PollConnector`` — per-repo container poll
* ``CheckpointedConnector`` — opaque per-batch cursor blob
* ``EventConnector`` — webhooks (push / issues / pull_request /
  repository / installation_repositories) with HMAC-256 verification
* ``SlimConnector`` — id-only prune enumeration
* ``SlimConnectorWithPermSync`` — collaborators + teams ACL mirror
* ``Resolver`` — failed blob / issue replay + force-push full-container
  refresh
* ``HierarchyConnector`` — Org → repo → directory tree
* ``OAuthConnector`` — App JWT → installation-token exchange + OAuth
  App user flow
* ``CredentialsConnector`` — App / PAT credential blob load

Auth: GitHub App (one installation per cc_pair; installation tokens
TTL 1h, rotated under per-cc_pair lock at 50% TTL per spec §5) OR
PAT (fine-grained / classic). The ``connector-github-*`` secret family
covers both shapes:

* App path: ``connector-github-app-id`` +
  ``connector-github-installation-id`` + ``connector-github-app-private-key``
* PAT path: ``connector-github-personal-access-token``
* Webhook: ``connector-github-webhook-secret``

F37-sanctioned change-detection lib: ``dulwich`` (pure-Python git, used
as the clone fallback for repos whose tree exceeds the 100k-entry
GitHub Trees API truncation threshold per spec §0). The ``dulwich``
import is confined to this plugin per F37.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` (key ``github``) — operators select it by
listing ``github`` in their ``connectors[]`` config. The connector is
flag-gated at introduce stage via the ``connector_github`` registry
entry (default off) so a misconfigured deploy never silently starts
polling GitHub's API.

Per F35 / F41 the module only imports from itself, ``kairix.core.*``
(Protocol surface + typed exceptions), and stdlib + ``httpx``. No
reach into other connectors, no reach into the extractor layer.

The plugin carries ``py.typed`` per F41 and is mypy-strict-clean.

See ``tests/bdd/features/connector_github.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.github.api_client import (
    ClientStatsSnapshot,
    GitHubApiClient,
    GitHubBlobRef,
    GitHubClientConfig,
    GitHubCommitRef,
    GitHubInstallationToken,
    GitHubIssueRef,
    GitHubRepoRef,
)
from kairix.connectors.github.connector import (
    TOPOLOGY_V2_GITHUB_FLAG,
    GitHubConnector,
    GitHubCredentials,
    make_connector,
)
from kairix.connectors.github.webhook import (
    HEADER_DELIVERY_ID,
    HEADER_EVENT_TYPE,
    HEADER_SIGNATURE_256,
    WebhookEnvelope,
    WebhookSignatureError,
    translate_event,
    verify_and_parse,
)

# F56 capability declaration — GitHub satisfies the full Wave-E
# capability set per spec §1. Frozen set so a future call-site can run
# an inventory check without rebinding the registry.
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
        "CredentialsConnector",
    }
)


__all__ = [
    "CAPABILITIES",
    "HEADER_DELIVERY_ID",
    "HEADER_EVENT_TYPE",
    "HEADER_SIGNATURE_256",
    "TOPOLOGY_V2_GITHUB_FLAG",
    "ClientStatsSnapshot",
    "GitHubApiClient",
    "GitHubBlobRef",
    "GitHubClientConfig",
    "GitHubCommitRef",
    "GitHubConnector",
    "GitHubCredentials",
    "GitHubInstallationToken",
    "GitHubIssueRef",
    "GitHubRepoRef",
    "WebhookEnvelope",
    "WebhookSignatureError",
    "make_connector",
    "translate_event",
    "verify_and_parse",
]
