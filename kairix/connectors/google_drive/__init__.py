"""Google Drive connector plugin — workspace corpus via Drive v3 REST.

First-party :class:`kairix.core.protocols.SourceConnector` implementation
for one Google Drive corpus (one OAuth grant = one workspace user's
view of Drive, including shared-with-me files). Pulls envelope +
binary content for each visible file via the Drive v3 ``/changes``
endpoint, then hands the bytes off to the kairix extractor registry
(markitdown / pptx / docx / xlsx / ocr / passthrough) for per-format
extraction. Source uri, sensitivity tier, and last-modified timestamp
travel through to every emitted chunk per F39.

Auth is OAuth2 bearer — the connector resolves the access token via
:func:`kairix.secrets.get_secret` at construction time. The token is
provisioned out-of-band; rotation on 401 raises
:class:`CredentialExpiredError` and the framework transitions the
cc_pair to a credential-renewal state.

Registered via ``[project.entry-points."kairix.connectors"]`` in
kairix's ``pyproject.toml`` — operators select it by listing
``google_drive`` in their ``connectors[]`` config, behind the
``topology_google_drive`` feature flag (introduce stage, default
off; see ``docs/architecture/feature-flag-architecture.md`` §3).

Default sensitivity tier is ``internal``. Operators routing
client-confidential or personal-tier corpora override via the
connector config's ``default_sensitivity`` key.

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38).

OAuth credential provisioning is tracked under GH #356 and is out of
scope for this code — the connector is ready-to-enable once the
operator-side KV secret lands.

See ``tests/bdd/features/connector_google_drive.feature`` for the
behaviour spec.
"""

from __future__ import annotations

from kairix.connectors.google_drive.client import (
    ChangesPage,
    DriveFileRef,
    GoogleDriveClient,
)
from kairix.connectors.google_drive.connector import (
    CONNECTOR_NAME,
    DEFAULT_SENSITIVITY,
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
    make_connector,
)

# F40 / plugin-version declaration — version surfaces as
# ``connector_version`` in the metadata staging table so re-syncs after
# a connector upgrade are tractable.
version: str = "0.1.0"

# F56 capability declaration. Google Drive satisfies the base
# SourceConnector plus CheckpointedConnector (Drive newStartPageToken
# is the canonical CheckpointedConnector pattern), CredentialsConnector
# (operator-supplied access token), plus the Wave E capabilities —
# PollConnector (per-container changes drain), SlimConnector (id-only
# enumeration for prune), Resolver (per-item failure replay), and
# HierarchyConnector (corpus parent-before-child).
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "SlimConnector",
        "HierarchyConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "CONNECTOR_NAME",
    "DEFAULT_SENSITIVITY",
    "ChangesPage",
    "DriveFileRef",
    "GoogleDriveClient",
    "GoogleDriveConnector",
    "GoogleDriveCorpusSpec",
    "GoogleDriveCredentials",
    "make_connector",
    "version",
]
