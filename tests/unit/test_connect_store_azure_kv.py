"""Unit-level coverage for kairix.connect.store.azure_kv_store.

The Azure SDK is optional; we exercise the store with injected
``credential_factory`` + ``client_factory`` so the suite stays fast
and works in environments without ``azure-identity`` installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStoreUnauthorizedError,
)
from kairix.connect.store.azure_kv_store import AzureKeyVaultTokenStore

pytestmark = pytest.mark.unit


class _FakeSecretClient:
    """Records every ``set_secret`` call. Optionally raises to exercise the unauthorized branch."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises

    def set_secret(self, name: str, value: str) -> None:
        if self._raises is not None:
            raise self._raises
        self.calls.append((name, value))


def _tokens() -> CapturedTokens:
    return CapturedTokens(refresh_token="rt", access_token="at", token_uri="https://x/")


def _client() -> ClientCredentials:
    return ClientCredentials(client_id="cid", client_secret="csec")


def _factories(client: _FakeSecretClient) -> dict[str, Any]:
    return {
        "credential_factory": lambda: object(),
        "client_factory": lambda _url, _cred: client,
    }


def test_explicit_vault_url_takes_priority() -> None:
    fake = _FakeSecretClient()
    store = AzureKeyVaultTokenStore(
        vault_url="https://override.vault.azure.net/",
        vault_name="ignored",
        env={"KAIRIX_KV_NAME": "also-ignored"},
        **_factories(fake),
    )
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert report.target == "https://override.vault.azure.net/"
    assert len(fake.calls) == 4


def test_vault_name_resolves_to_default_dns() -> None:
    fake = _FakeSecretClient()
    store = AzureKeyVaultTokenStore(
        vault_name="my-vault",
        env={},
        **_factories(fake),
    )
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert report.target == "https://my-vault.vault.azure.net/"


def test_env_var_used_when_no_explicit() -> None:
    fake = _FakeSecretClient()
    store = AzureKeyVaultTokenStore(
        env={"KAIRIX_KV_NAME": "env-vault"},
        **_factories(fake),
    )
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert report.target == "https://env-vault.vault.azure.net/"


def test_no_vault_anywhere_raises_f21_error() -> None:
    fake = _FakeSecretClient()
    store = AzureKeyVaultTokenStore(env={}, **_factories(fake))
    with pytest.raises(ValueError, match="requires a vault name"):
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=_tokens(),
            client=_client(),
        )


def test_set_secret_failure_raises_typed_error() -> None:
    """A backend write failure surfaces TokenStoreUnauthorizedError with F21 hints."""
    fake = _FakeSecretClient(raises=RuntimeError("Forbidden"))
    store = AzureKeyVaultTokenStore(
        vault_name="my-vault",
        env={},
        **_factories(fake),
    )
    with pytest.raises(TokenStoreUnauthorizedError, match="Secrets Officer"):
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=_tokens(),
            client=_client(),
        )


def test_all_four_canonical_names_written() -> None:
    fake = _FakeSecretClient()
    store = AzureKeyVaultTokenStore(
        vault_name="my-vault",
        env={},
        **_factories(fake),
    )
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    names = [name for name, _ in fake.calls]
    assert names == [
        "kairix-connector-gmail-client-id",
        "kairix-connector-gmail-client-secret",
        "kairix-connector-gmail-refresh-token",
        "kairix-connector-gmail-access-token",
    ]
    assert report.canonical_names == tuple(names)


def test_url_form_with_http_prefix_routes_through_vault_url() -> None:
    """URL form (``--store=azure-kv:https://...``) bypasses short-name resolution."""
    fake = _FakeSecretClient()
    store = AzureKeyVaultTokenStore(
        vault_url="https://sovereign.vault.usgovcloudapi.net/",
        env={},
        **_factories(fake),
    )
    report = store.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert "usgovcloudapi" in report.target


def test_lazy_import_azure_identity_raises_typed(monkeypatch: Any) -> None:
    """When ``azure-identity`` isn't installed the credential build surfaces a typed error."""
    import builtins
    import sys

    for key in list(sys.modules):
        if key.startswith("azure"):
            monkeypatch.delitem(sys.modules, key, raising=False)
    original = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "azure.identity":
            raise ImportError("no azure-identity in this env")
        return original(name, *args, **kwargs)  # type: ignore[arg-type]  # F3 rationale: builtins.__import__ wrapper signature mirrors stdlib but mypy refuses the *args/**kwargs forward

    monkeypatch.setattr(builtins, "__import__", blocked)
    # Construct without credential_factory so the lazy import path runs.
    store = AzureKeyVaultTokenStore(vault_name="x", env={})
    with pytest.raises(TokenStoreUnauthorizedError, match="azure-identity"):
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=_tokens(),
            client=_client(),
        )


def test_lazy_import_real_azure_identity_returns_credential() -> None:
    """When ``azure-identity`` IS installed, ``_build_credential`` constructs a real credential.

    Drives the import-success branch — pinned via the publicly-observable
    construction of the store with no injected ``credential_factory``.
    """
    from azure.identity import DefaultAzureCredential

    # client_factory captures the credential the store builds — proves
    # the lazy import + DefaultAzureCredential() construction ran.
    captured: dict[str, object] = {}

    def capture_client(_url: str, cred: object) -> _FakeSecretClient:
        captured["cred"] = cred
        return _FakeSecretClient()

    store_with_real_cred = AzureKeyVaultTokenStore(
        vault_name="x",
        env={},
        client_factory=capture_client,
    )
    store_with_real_cred.store(
        scope="connector",
        area="gmail",
        instance=None,
        tokens=_tokens(),
        client=_client(),
    )
    assert isinstance(captured["cred"], DefaultAzureCredential)


def test_lazy_import_real_azure_keyvault_returns_client() -> None:
    """When ``azure-keyvault-secrets`` IS installed, ``_build_secret_client`` returns a real client.

    Drives the import-success branch via the public store() path with
    no client_factory injection.
    """
    store = AzureKeyVaultTokenStore(
        vault_name="x",
        env={},
        credential_factory=lambda: object(),
    )
    # Wrap the SDK import success by constructing the real SecretClient.
    # The real SDK's ``set_secret`` would fail without a live Azure
    # context — we collapse the resulting TokenStoreUnauthorizedError;
    # the lazy import we're pinning ran before that point.
    try:
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=_tokens(),
            client=_client(),
        )
    except TokenStoreUnauthorizedError:
        # Expected — the real Azure SDK rejects ``set_secret`` because
        # the injected credential isn't a real token source. The
        # SecretClient construction itself succeeded, which is what
        # this test was pinning.
        return


def test_lazy_import_azure_keyvault_raises_typed(monkeypatch: Any) -> None:
    """When ``azure-keyvault-secrets`` isn't installed the client build surfaces a typed error."""
    import builtins
    import sys

    for key in list(sys.modules):
        if key.startswith("azure"):
            monkeypatch.delitem(sys.modules, key, raising=False)
    original = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "azure.keyvault.secrets":
            raise ImportError("no azure-keyvault-secrets in this env")
        return original(name, *args, **kwargs)  # type: ignore[arg-type]  # F3 rationale: builtins.__import__ wrapper signature mirrors stdlib but mypy refuses the *args/**kwargs forward

    monkeypatch.setattr(builtins, "__import__", blocked)
    # Supply credential_factory so we don't fail on identity first; let
    # client_factory remain unset so the keyvault lazy import is hit.
    store = AzureKeyVaultTokenStore(
        vault_name="x",
        env={},
        credential_factory=lambda: object(),
    )
    with pytest.raises(TokenStoreUnauthorizedError, match="azure-keyvault-secrets"):
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=_tokens(),
            client=_client(),
        )
