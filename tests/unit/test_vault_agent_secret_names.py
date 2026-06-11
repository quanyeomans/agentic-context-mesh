"""Vault-agent sidecar secret-name mapping (#479).

The sidecar at ``docker/vault-agent/fetch_secrets.py`` must emit the
canonical env-var names from ``docs/operations/secrets-configuration.md``
(``KAIRIX_PROVIDER_LLM_API_KEY`` etc.) AND keep the legacy aliases
(``KAIRIX_LLM_API_KEY`` etc.) during the transition window, resolving
canonical KV secret names (``kairix-provider-*`` / ``kairix-infra-*``)
first with fallback to the pre-canonical short names so existing vaults
keep working.

The module is loaded by file path — it is a Docker sidecar script, not
part of the kairix package (same mechanism as
``tests/plugins/test_kairix_memory_prompt.py``). Resolution is driven
through the pure ``resolve_secret_env`` seam with a dict-backed fake
fetch callable — no Azure SDK, no monkeypatching.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_MODULE_PATH = Path(__file__).resolve().parents[2] / "docker" / "vault-agent" / "fetch_secrets.py"

_CANONICAL_ENV_VARS = (
    "KAIRIX_PROVIDER_LLM_API_KEY",
    "KAIRIX_PROVIDER_LLM_ENDPOINT",
    "KAIRIX_PROVIDER_LLM_MODEL",
    "KAIRIX_PROVIDER_EMBED_API_KEY",
    "KAIRIX_PROVIDER_EMBED_ENDPOINT",
    "KAIRIX_PROVIDER_EMBED_MODEL",
    "KAIRIX_INFRA_NEO4J_PASSWORD",  # pragma: allowlist secret — env-var NAME, not a value
)

# Legacy aliases kept for the transition window — remove with #369.
_LEGACY_ENV_VARS = (
    "KAIRIX_LLM_API_KEY",
    "KAIRIX_LLM_ENDPOINT",
    "KAIRIX_LLM_MODEL",
    "KAIRIX_EMBED_API_KEY",
    "KAIRIX_EMBED_ENDPOINT",
    "KAIRIX_EMBED_MODEL",
    "KAIRIX_NEO4J_PASSWORD",  # pragma: allowlist secret — env-var NAME, not a value
)

_CANONICAL_VAULT = {
    "kairix-provider-llm-api-key": "llm-key-canonical",
    "kairix-provider-llm-endpoint": "llm-endpoint-canonical",
    "kairix-provider-llm-model": "llm-model-canonical",
    "kairix-provider-embed-api-key": "embed-key-canonical",
    "kairix-provider-embed-endpoint": "embed-endpoint-canonical",
    "kairix-provider-embed-model": "embed-model-canonical",
    "kairix-infra-neo4j-password": "neo4j-pass-canonical",  # pragma: allowlist secret — fixture value
}

_LEGACY_VAULT = {
    "kairix-llm-api-key": "llm-key-legacy",
    "kairix-llm-endpoint": "llm-endpoint-legacy",
    "kairix-llm-model": "llm-model-legacy",
    "kairix-embed-api-key": "embed-key-legacy",
    "kairix-embed-endpoint": "embed-endpoint-legacy",
    "kairix-embed-model": "embed-model-legacy",
    "kairix-neo4j-password": "neo4j-pass-legacy",  # pragma: allowlist secret — fixture value
}


@pytest.fixture(scope="module")
def fetch_secrets_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vault_agent_fetch_secrets", _MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_vault_emits_canonical_and_legacy_env_vars(
    fetch_secrets_module: ModuleType,
) -> None:
    """A vault holding only canonical names fans out to both env-var sets."""
    resolved = fetch_secrets_module.resolve_secret_env(_CANONICAL_VAULT.get)

    assert set(resolved) == set(_CANONICAL_ENV_VARS) | set(_LEGACY_ENV_VARS)
    assert resolved["KAIRIX_PROVIDER_LLM_API_KEY"] == "llm-key-canonical"  # pragma: allowlist secret — fixture value
    assert resolved["KAIRIX_LLM_API_KEY"] == "llm-key-canonical"  # pragma: allowlist secret — fixture value
    assert resolved["KAIRIX_INFRA_NEO4J_PASSWORD"] == "neo4j-pass-canonical"  # pragma: allowlist secret
    assert resolved["KAIRIX_NEO4J_PASSWORD"] == "neo4j-pass-canonical"  # pragma: allowlist secret


def test_legacy_vault_resolves_via_fallback_names(
    fetch_secrets_module: ModuleType,
) -> None:
    """A pre-canonical vault (short KV names only) still resolves everything."""
    resolved = fetch_secrets_module.resolve_secret_env(_LEGACY_VAULT.get)

    assert set(resolved) == set(_CANONICAL_ENV_VARS) | set(_LEGACY_ENV_VARS)
    assert resolved["KAIRIX_PROVIDER_EMBED_ENDPOINT"] == "embed-endpoint-legacy"
    assert resolved["KAIRIX_EMBED_ENDPOINT"] == "embed-endpoint-legacy"
    assert resolved["KAIRIX_INFRA_NEO4J_PASSWORD"] == "neo4j-pass-legacy"  # pragma: allowlist secret


def test_canonical_kv_name_wins_over_legacy_fallback(
    fetch_secrets_module: ModuleType,
) -> None:
    """When both KV generations hold a secret, the canonical name wins."""
    both = {**_LEGACY_VAULT, **_CANONICAL_VAULT}

    resolved = fetch_secrets_module.resolve_secret_env(both.get)

    assert resolved["KAIRIX_PROVIDER_LLM_API_KEY"] == "llm-key-canonical"  # pragma: allowlist secret — fixture value
    assert resolved["KAIRIX_LLM_API_KEY"] == "llm-key-canonical"  # pragma: allowlist secret — fixture value


def test_empty_vault_resolves_nothing(fetch_secrets_module: ModuleType) -> None:
    """Missing secrets are skipped — same semantics the sidecar always had."""
    resolved = fetch_secrets_module.resolve_secret_env(lambda _name: None)

    assert resolved == {}


def test_every_spec_lists_canonical_env_var_first(
    fetch_secrets_module: ModuleType,
) -> None:
    """Spec shape contract: canonical env var leads, KV name is canonical.

    The first env var of every spec must be the canonical
    ``KAIRIX_<SCOPE>_...`` form derived from its KV name (uppercase,
    hyphens → underscores) per docs/operations/secrets-configuration.md.
    """
    for spec in fetch_secrets_module.SECRET_SPECS:
        derived = spec.kv_name.replace("-", "_").upper()
        assert spec.env_vars[0] == derived
        assert spec.kv_name.startswith(("kairix-provider-", "kairix-infra-"))
