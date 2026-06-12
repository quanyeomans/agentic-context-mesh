"""Contract: both credential-write surfaces emit the names the connectors read.

GitHub App credentials were unusable end-to-end (review finding H2)
because the two write surfaces disagreed with the read side:

* ``kairix connect github-app`` (CLI) stored the repurposed dataclass
  slots as ``client-id`` / ``client-secret``,
* the setup wizard hand-rolled the correct ``app-id`` /
  ``app-private-key`` triple,
* the GitHub connector's credential resolver reads ``app-id`` /
  ``app-private-key`` / ``installation-id`` — so a CLI-connected App
  resolved ``app_id=None`` and could never mint installation tokens.

This contract pins all three corners together, per provider:

1. The CLI-store leaf names (every ``TokenStore`` derives through
   :func:`kairix.connect.store.leaves.leaf_pairs` with the store's
   ``service_area``) equal the wizard's
   :func:`kairix.platform.setup.source_oauth.source_secret_leaves`
   names — exact match, order included.
2. The connector resolver's required read leaves are a subset of the
   written set, driven through the connector's exported leaf constants
   where the connector publishes them (GitHub).

Sabotage-proof (executed): removed the ``"github"`` entry from
``SERVICE_LEAF_OVERRIDES`` — the github rows failed on both assertions
(CLI wrote client-id/client-secret; the App triple went unwritten).
Restored.

F15: assertions reference NAMES only; the fixture values are fake and
never logged.
"""

from __future__ import annotations

import pytest

from kairix.connect.protocols import CapturedTokens, ClientCredentials
from kairix.connect.store.leaves import leaf_pairs
from kairix.connectors.github.connector import GITHUB_APP_LEAVES
from kairix.platform.setup.source_oauth import source_secret_leaves
from kairix.secrets.naming import canonical_secret_name

pytestmark = pytest.mark.contract

_FAKE_PEM = (  # pragma: allowlist secret — fake key body
    "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n"  # pragma: allowlist secret — fake
)

# One row per OAuth-connectable provider the wizard + CLI both serve:
# (service_area, instance, client, tokens, required_read_leaves).
#
# ``required_read_leaves`` mirrors each connector's credential-resolver
# read site:
#   * github — exported as ``GITHUB_APP_LEAVES`` next to
#     ``_resolve_credentials_from_secrets`` in
#     kairix/connectors/github/connector.py (the App-mode triple).
#   * slack — ``kairix/connectors/slack/connector.py`` requires
#     ``bot-token`` (app-token / client-id / client-secret optional).
#   * gmail — ``kairix/connectors/gmail/connector.py`` requires
#     client-id + client-secret + refresh-token (access-token optional).
_PARITY_ROWS: tuple[tuple[str, str | None, ClientCredentials, CapturedTokens, tuple[str, ...]], ...] = (
    (
        "github",
        None,
        ClientCredentials(client_id="42", client_secret=_FAKE_PEM),
        CapturedTokens(
            refresh_token="",
            access_token="ghs_fake",
            token_uri="https://api.github.com/app/installations/access_tokens",
            metadata={"installation-id": "70000"},
        ),
        GITHUB_APP_LEAVES,
    ),
    (
        "slack",
        "agent-alpha-workspace",
        ClientCredentials(client_id="slack-cid", client_secret="slack-csec"),  # pragma: allowlist secret
        CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri="https://slack.test/token",
            bot_token="xoxb-fake",
        ),
        ("bot-token",),
    ),
    (
        "gmail",
        None,
        ClientCredentials(client_id="google-cid", client_secret="google-csec"),  # pragma: allowlist secret
        CapturedTokens(
            refresh_token="fake-refresh",
            access_token="fake-access",
            token_uri="https://oauth2.googleapis.com/token",
        ),
        ("client-id", "client-secret", "refresh-token"),
    ),
)

_ROW_IDS = tuple(row[0] for row in _PARITY_ROWS)


def _wizard_names(
    area: str,
    instance: str | None,
    client: ClientCredentials,
    tokens: CapturedTokens,
) -> list[str]:
    """The wizard's written canonical secret names."""
    return [name for name, _ in source_secret_leaves(area, instance, client, tokens)]


@pytest.mark.parametrize(("area", "instance", "client", "tokens", "_required"), _PARITY_ROWS, ids=_ROW_IDS)
def test_cli_store_and_wizard_write_identical_name_sets(
    area: str,
    instance: str | None,
    client: ClientCredentials,
    tokens: CapturedTokens,
    _required: tuple[str, ...],
) -> None:
    """Surface parity: CLI-store derivation == wizard derivation, exactly."""
    cli_leaves = [leaf for leaf, _ in leaf_pairs(client, tokens, service_area=area)]
    cli_names = [canonical_secret_name("connector", area, instance, leaf) for leaf in cli_leaves]
    assert cli_names == _wizard_names(area, instance, client, tokens)
    assert cli_names, f"{area}: expected at least one written name"


@pytest.mark.parametrize(("area", "instance", "client", "tokens", "required"), _PARITY_ROWS, ids=_ROW_IDS)
def test_connector_required_read_leaves_are_all_written(
    area: str,
    instance: str | None,
    client: ClientCredentials,
    tokens: CapturedTokens,
    required: tuple[str, ...],
) -> None:
    """Read parity: every leaf the connector requires gets written by both surfaces."""
    cli_leaves = {leaf for leaf, _ in leaf_pairs(client, tokens, service_area=area)}
    wizard_names = set(_wizard_names(area, instance, client, tokens))
    missing_from_cli = set(required) - cli_leaves
    missing_from_wizard = {
        leaf for leaf in required if canonical_secret_name("connector", area, instance, leaf) not in wizard_names
    }
    assert not missing_from_cli, f"{area}: CLI store never writes {sorted(missing_from_cli)}"
    assert not missing_from_wizard, f"{area}: wizard never writes {sorted(missing_from_wizard)}"


def test_github_app_leaves_constant_is_the_app_mode_triple() -> None:
    """The exported read-set constant carries the documented App triple."""
    assert GITHUB_APP_LEAVES == ("app-id", "app-private-key", "installation-id")
