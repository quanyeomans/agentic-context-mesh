"""Unit-level coverage for the dex_crm connector's secrets-loader path.

The connector's __init__ accepts a :class:`SecretsResolver` injection
seam; when supplied AND no explicit ``client=`` is provided, it eagerly
resolves the canonical ``connector/dex/-/api-key`` identity tuple via
the loader and binds it into a pre-bound :class:`ApiKeyAuth` shape so
the per-request `.headers(...)` call never re-walks the legacy chain.

These tests pin the canonical-surface contract end-to-end through
:class:`tests.fakes.FakeSecretsLoader` — no monkey-patching, no
``os.environ`` writes (F1 / F2 clean).
"""

from __future__ import annotations

import pytest

from kairix.connectors.dex_crm import DexCrmConnector, make_connector
from kairix.transport.auth.api_key import MissingCredentialsError
from tests.fakes import FakeSecretsLoader

pytestmark = pytest.mark.unit


def test_dex_crm_loads_secrets_via_loader() -> None:
    """DexCrmConnector.__init__ calls loader.require() for the api-key.

    Pins the canonical-surface contract: the connector eagerly asks the
    injected SecretsResolver for the ``connector/dex/-/api-key`` identity
    tuple at construction time so configuration errors surface
    immediately rather than at first poll.

    Sabotage-proof: replace ``secrets.require(*_SECRET_SCOPE_API_KEY)``
    in DexCrmConnector.__init__ with a literal token → the loader's
    get_calls remains empty and the assertion below flunks.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "dex", None, "api-key"): "loader-api-token",
        },
    )
    DexCrmConnector(secrets=loader)
    assert ("connector", "dex", None, "api-key") in loader.get_calls


def test_dex_crm_raises_missing_credentials_when_loader_lacks_api_key() -> None:
    """A loader with no api-key bound surfaces MissingCredentialsError.

    The connector translates the loader's typed :class:`SecretNotFoundError`
    into the connector's own :class:`MissingCredentialsError` so the worker
    dead-letter surface stays uniform across the auth-error shape.

    Sabotage-proof: drop the try/except block in __init__ and the test
    catches a SecretNotFoundError instead of MissingCredentialsError.
    """
    loader = FakeSecretsLoader()  # no values bound
    with pytest.raises(MissingCredentialsError, match="api-key secret is not configured"):
        DexCrmConnector(secrets=loader)


def test_dex_crm_loader_value_is_threaded_into_pre_bound_auth() -> None:
    """The loader's resolved value is what the client's auth header carries.

    Drives the contract end-to-end: build a connector with a loader, then
    have the client's pre-bound auth produce its headers — the bearer
    token must be the loader-supplied value (not a re-walked legacy chain
    value, not a placeholder, not None).

    Sabotage-proof: replace the ``api_key`` passed to
    :class:`_PreBoundApiKeyAuth` with a literal "sabotage" string →
    headers assertion below catches the wrong token.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "dex", None, "api-key"): "loader-secret-token",
        },
    )
    connector = DexCrmConnector(secrets=loader)
    # Drive the client's auth surface directly to inspect what bearer
    # the connector now carries — public API only.
    headers = connector._client.auth.headers("any-name")
    assert headers.mapping == {"Authorization": "Bearer loader-secret-token"}


def test_dex_crm_explicit_client_bypasses_loader_path() -> None:
    """When ``client=`` is supplied, the loader is never consulted.

    The DI seam protocol: an explicit client takes precedence over the
    loader-driven default. Pins the "tests pass a recording client"
    pattern unchanged from before the refactor.

    Sabotage-proof: remove the ``if client is not None`` early-return
    branch in __init__ and the loader.get_calls assertion below catches
    the loader being consulted.
    """
    from kairix.connectors.dex_crm.client import DexCrmClient

    loader = FakeSecretsLoader()  # no values bound
    explicit_client = DexCrmClient()
    # Should NOT raise even though loader has no values — explicit client
    # path takes precedence.
    connector = DexCrmConnector(client=explicit_client, secrets=loader)
    assert connector._client is explicit_client
    assert loader.get_calls == []


def test_dex_crm_no_loader_keeps_legacy_lazy_path() -> None:
    """When ``secrets=None``, the connector retains its historic shape.

    Backwards-compatibility contract: code that doesn't yet thread the
    loader through (older tests, downstream callers mid-migration) keeps
    working — construction stays cheap, the auth path is the historic
    :class:`ApiKeyAuth` with on-first-request resolution.

    Sabotage-proof: change the ``else`` branch to call ``loader.require``
    with a default loader → the connector silently re-walks the env at
    construction time, the assertion on the auth-class identity below
    flunks.
    """
    from kairix.transport.auth.api_key import ApiKeyAuth

    connector = DexCrmConnector()
    # The legacy path uses a fresh ApiKeyAuth (not the pre-bound subclass).
    assert type(connector._client.auth) is ApiKeyAuth


def test_make_connector_threads_loader_into_dex_crm_connector() -> None:
    """make_connector(..., secrets=loader) wires the loader through.

    F45-style "no env vars in tests" contract: the canonical surface
    works end-to-end through make_connector + a FakeSecretsLoader without
    any monkey-patching.

    Sabotage-proof: drop the ``secrets=secrets`` kwarg from the
    ``DexCrmConnector(...)`` call in make_connector → the loader's
    get_calls stays empty and the assertion below flunks.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "dex", None, "api-key"): "make-connector-token",
        },
    )
    connector = make_connector({}, secrets=loader)
    # Connector built via the canonical surface — the loader was asked
    # for the api-key identity tuple.
    assert ("connector", "dex", None, "api-key") in loader.get_calls
    # And the bearer is the loader-supplied value.
    headers = connector._client.auth.headers("any-name")
    assert headers.mapping == {"Authorization": "Bearer make-connector-token"}
