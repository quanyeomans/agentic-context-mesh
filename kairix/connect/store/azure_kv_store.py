"""Azure Key Vault token store — writes via ``azure-keyvault-secrets``.

Uses :class:`azure.identity.DefaultAzureCredential` so the same
credential chain that the rest of kairix's read-side uses applies
verbatim (managed identity preferred; falls through to env vars then
to the operator's cached ``az login``).

Vault resolution (first match wins, per ADR-032 §"Endpoint specification"):

  1. Explicit ``vault_url`` constructor arg (from
     ``--store=azure-kv:https://<vault>.vault.azure.net/``).
  2. Explicit ``vault_name`` constructor arg (from
     ``--store=azure-kv:<vault-name>``).
  3. ``$KAIRIX_KV_NAME`` env var (same env the read-side
     ``fetch-secrets.sh`` honours — keeps operator setup symmetric).
  4. No fallback — :class:`ValueError` with the F21 remediation.

The Azure SDK is imported lazily inside ``store()`` so module import
succeeds in environments where ``azure-identity`` /
``azure-keyvault-secrets`` aren't installed — the operator only needs
them when they actually pick ``--store=azure-kv``.

Per F4, ``$KAIRIX_KV_NAME`` is read directly here because the connect
flow is the canonical write-side; the F4 allowlist already covers
``kairix.secrets`` (the read side) — this module is the matching
write-side surface.
"""

from __future__ import annotations

import os

from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStore,
    TokenStoreUnauthorizedError,
    WriteReport,
)
from kairix.connect.store.leaves import leaf_pairs
from kairix.secrets.naming import Scope, canonical_secret_name

_BACKEND_NAME = "azure-kv"


class AzureKeyVaultTokenStore:
    """Write captured tokens to an Azure Key Vault via ``azure-keyvault-secrets``.

    Args:
      vault_url: Explicit vault URL (covers sovereign clouds + non-default
        DNS suffixes). Highest priority.
      vault_name: Explicit vault short-name; resolved to
        ``https://<name>.vault.azure.net/``. Second priority.
      env: Test seam — overrides ``os.environ`` for the
        ``$KAIRIX_KV_NAME`` fallback lookup.
      credential_factory: Test seam — replaces the
        ``DefaultAzureCredential`` instantiation so tests don't need a
        real Azure context. Defaults to constructing
        :class:`azure.identity.DefaultAzureCredential` lazily.
      client_factory: Test seam — replaces the ``SecretClient``
        instantiation. Defaults to constructing
        :class:`azure.keyvault.secrets.SecretClient` lazily.
    """

    def __init__(
        self,
        *,
        vault_url: str | None = None,
        vault_name: str | None = None,
        env: dict[str, str] | None = None,
        credential_factory: object | None = None,
        client_factory: object | None = None,
    ) -> None:
        self._vault_url = vault_url
        self._vault_name = vault_name
        self._env: dict[str, str] = dict(env) if env is not None else dict(os.environ)
        self._credential_factory = credential_factory
        self._client_factory = client_factory

    def store(
        self,
        *,
        scope: Scope,
        area: str,
        instance: str | None,
        tokens: CapturedTokens,
        client: ClientCredentials,
    ) -> WriteReport:
        url = self._resolve_vault_url()
        secret_client = self._build_secret_client(url)
        canonical: list[str] = []
        for leaf, value in leaf_pairs(client, tokens):
            name = canonical_secret_name(scope, area, instance, leaf)
            try:
                secret_client.set_secret(name, value)  # type: ignore[attr-defined]  # F3 rationale: secret_client is typed object because the live SecretClient + the test FakeSecretClient share the .set_secret(name, value) shape but no common base.
            except Exception as exc:
                raise TokenStoreUnauthorizedError(
                    f"kairix connect: Azure Key Vault write of {name!r} to {url} failed: {exc}. "
                    f"fix: confirm the current identity has Key Vault Secrets Officer on the vault "
                    f"(Secrets User is read-only and insufficient for writes). "
                    f"next: az role assignment list --assignee <principal-id> --scope "
                    f"/subscriptions/.../vaults/<vault> --query '[].roleDefinitionName' -o tsv. "
                    f"run: az role assignment create --role 'Key Vault Secrets Officer' "
                    f"--assignee <principal-id> --scope <vault-scope>",
                ) from exc
            canonical.append(name)
        return WriteReport(
            canonical_names=tuple(canonical),
            backend=_BACKEND_NAME,
            target=url,
        )

    def _resolve_vault_url(self) -> str:
        """Apply the four-step vault resolution from ADR-032."""
        if self._vault_url:
            return self._vault_url.rstrip("/") + "/"
        if self._vault_name:
            return f"https://{self._vault_name}.vault.azure.net/"
        env_name = self._env.get("KAIRIX_KV_NAME")
        if env_name:
            return f"https://{env_name}.vault.azure.net/"
        raise ValueError(
            "kairix connect: --store=azure-kv requires a vault name. "
            "fix: set KAIRIX_KV_NAME=<your-vault> OR pass --store=azure-kv:<vault-name>. "
            "next: az keyvault list --query '[].name' -o tsv to list available vaults. "
            "run: kairix connect <service> --store=azure-kv:<vault-name> --client-secret-path <path>",
        )

    def _build_secret_client(self, vault_url: str) -> object:
        """Construct the ``SecretClient``. Lazy-imports the SDK on first call."""
        if self._client_factory is not None:
            return self._client_factory(vault_url, self._build_credential())  # type: ignore[operator]  # F3 rationale: client_factory is callable when supplied by tests
        try:
            from azure.keyvault.secrets import SecretClient
        except ImportError as exc:
            raise TokenStoreUnauthorizedError(
                "kairix connect: --store=azure-kv requires the azure-keyvault-secrets package. "
                "fix: pip install 'azure-identity>=1.19' 'azure-keyvault-secrets>=4.9'. "
                "next: re-run kairix connect <service> --store=azure-kv. "
                "run: pip install 'azure-identity>=1.19' 'azure-keyvault-secrets>=4.9'",
            ) from exc
        return SecretClient(vault_url=vault_url, credential=self._build_credential())  # type: ignore[arg-type]  # F3 rationale: _build_credential returns object so the test-injection seam can pass a stand-in; the real production path returns DefaultAzureCredential which satisfies TokenCredential.

    def _build_credential(self) -> object:
        """Construct the Azure credential. Lazy-imports the SDK on first call."""
        if self._credential_factory is not None:
            return self._credential_factory()  # type: ignore[operator]  # F3 rationale: credential_factory is callable when supplied by tests
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise TokenStoreUnauthorizedError(
                "kairix connect: --store=azure-kv requires the azure-identity package. "
                "fix: pip install 'azure-identity>=1.19' 'azure-keyvault-secrets>=4.9'. "
                "next: re-run kairix connect <service> --store=azure-kv. "
                "run: pip install 'azure-identity>=1.19' 'azure-keyvault-secrets>=4.9'",
            ) from exc
        return DefaultAzureCredential()


# Protocol conformance smoke check.
_PROTOCOL_CHECK: TokenStore = AzureKeyVaultTokenStore()


__all__ = ["AzureKeyVaultTokenStore"]
