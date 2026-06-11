#!/usr/bin/env python3
"""
vault-agent: fetch secrets from Azure Key Vault and write to a tmpfs secrets file.

Runs as a Docker sidecar alongside the kairix service. Fetches all required
secrets at startup, writes them to /run/secrets/kairix.env, creates
/run/secrets/.ready to signal readiness, then refreshes on a timer.

Authentication via DefaultAzureCredential — supports (in order):
  1. Managed Identity  — recommended on Azure VMs (AZURE_CLIENT_ID optional)
  2. Service Principal — set AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID
  3. Azure CLI         — for local dev (`az login`)

Required environment variables:
  KAIRIX_KV_NAME   Azure Key Vault name (e.g. kv-example)

Optional:
  SECRETS_DIR              Where to write the secrets file (default: /run/secrets)
  REFRESH_INTERVAL_SECONDS How often to re-fetch from KV (default: 3600)

Secrets fetched (canonical KV secret name → env vars written to file):
  kairix-provider-llm-api-key     → KAIRIX_PROVIDER_LLM_API_KEY     (+ KAIRIX_LLM_API_KEY)
  kairix-provider-llm-endpoint    → KAIRIX_PROVIDER_LLM_ENDPOINT    (+ KAIRIX_LLM_ENDPOINT)
  kairix-provider-llm-model       → KAIRIX_PROVIDER_LLM_MODEL       (+ KAIRIX_LLM_MODEL)
  kairix-provider-embed-api-key   → KAIRIX_PROVIDER_EMBED_API_KEY   (+ KAIRIX_EMBED_API_KEY)
  kairix-provider-embed-endpoint  → KAIRIX_PROVIDER_EMBED_ENDPOINT  (+ KAIRIX_EMBED_ENDPOINT)
  kairix-provider-embed-model     → KAIRIX_PROVIDER_EMBED_MODEL     (+ KAIRIX_EMBED_MODEL)
  kairix-infra-neo4j-password     → KAIRIX_INFRA_NEO4J_PASSWORD     (+ KAIRIX_NEO4J_PASSWORD)

Canonical names follow docs/operations/secrets-configuration.md
(kairix-<scope>-<area>[-<instance>]-<leaf>). Vaults created before the
canonical schema may still hold the pre-canonical short names
(kairix-llm-api-key etc.); each spec lists those as ``kv_fallbacks`` so
existing vaults keep working during the transition window (#479). The
parenthesised env vars are legacy aliases — remove with #369.
"""

import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s vault-agent %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("vault-agent")

SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/run/secrets"))
SECRETS_FILE = SECRETS_DIR / "kairix.env"
READY_FILE = SECRETS_DIR / ".ready"
KV_NAME = os.environ.get("KAIRIX_KV_NAME", "")
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL_SECONDS", "3600"))


@dataclass(frozen=True)
class SecretSpec:
    """One secret to fetch: canonical KV name, env vars to emit, KV fallbacks.

    ``kv_name`` is the canonical Key Vault secret name (fetched first).
    ``env_vars`` lists every env var the resolved value is written under —
    canonical name first, then the legacy aliases kept for the transition
    window (remove with #369). ``kv_fallbacks`` lists pre-canonical KV
    names tried in order when the canonical name is absent, so existing
    vaults keep working (#479).
    """

    kv_name: str
    env_vars: tuple[str, ...]
    kv_fallbacks: tuple[str, ...] = field(default=())


SECRET_SPECS: tuple[SecretSpec, ...] = (
    SecretSpec(
        "kairix-provider-llm-api-key",
        ("KAIRIX_PROVIDER_LLM_API_KEY", "KAIRIX_LLM_API_KEY"),
        ("kairix-llm-api-key",),
    ),
    SecretSpec(
        "kairix-provider-llm-endpoint",
        ("KAIRIX_PROVIDER_LLM_ENDPOINT", "KAIRIX_LLM_ENDPOINT"),
        ("kairix-llm-endpoint",),
    ),
    SecretSpec(
        "kairix-provider-llm-model",
        ("KAIRIX_PROVIDER_LLM_MODEL", "KAIRIX_LLM_MODEL"),
        ("kairix-llm-model",),
    ),
    SecretSpec(
        "kairix-provider-embed-api-key",
        ("KAIRIX_PROVIDER_EMBED_API_KEY", "KAIRIX_EMBED_API_KEY"),
        ("kairix-embed-api-key",),
    ),
    SecretSpec(
        "kairix-provider-embed-endpoint",
        ("KAIRIX_PROVIDER_EMBED_ENDPOINT", "KAIRIX_EMBED_ENDPOINT"),
        ("kairix-embed-endpoint",),
    ),
    SecretSpec(
        "kairix-provider-embed-model",
        ("KAIRIX_PROVIDER_EMBED_MODEL", "KAIRIX_EMBED_MODEL"),
        ("kairix-embed-model",),
    ),
    SecretSpec(
        # KAIRIX_NEO4J_PASSWORD is NOT only an alias yet: the graph layer
        # (kairix.secrets.neo4j_password) and docker-compose interpolation
        # still read it today. Keep emitting it until #369 retires it.
        "kairix-infra-neo4j-password",  # pragma: allowlist secret — secret NAME, not a value
        ("KAIRIX_INFRA_NEO4J_PASSWORD", "KAIRIX_NEO4J_PASSWORD"),  # pragma: allowlist secret
        ("kairix-neo4j-password",),  # pragma: allowlist secret — secret NAME, not a value
    ),
)


def resolve_secret_env(fetch: Callable[[str], str | None]) -> dict[str, str]:
    """Resolve every spec through ``fetch`` and fan values out to env vars.

    For each :class:`SecretSpec`, the canonical KV name is tried first,
    then each legacy fallback in order. A resolved value is emitted under
    every env var the spec declares (canonical + legacy aliases). Specs
    that resolve nowhere are skipped — same missing-secret semantics as
    before.
    """
    fetched: dict[str, str] = {}
    for spec in SECRET_SPECS:
        value = fetch(spec.kv_name)
        if value is None:
            for legacy_name in spec.kv_fallbacks:
                value = fetch(legacy_name)
                if value is not None:
                    break
        if value is not None:
            for env_var in spec.env_vars:
                fetched[env_var] = value
    return fetched


def fetch_from_keyvault() -> dict[str, str]:
    """
    Fetch all secrets from Azure Key Vault.

    Returns a dict of {env_var_name: secret_value} for successfully fetched
    secrets. Missing secrets are logged as warnings but do not abort the run.
    """
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    kv_uri = f"https://{KV_NAME}.vault.azure.net"
    logger.info("Connecting to Key Vault: %s", kv_uri)

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=kv_uri, credential=credential)

    fetched = resolve_secret_env(lambda name: _fetch_single_secret(client, name))

    logger.info(
        "Resolved %d env var(s) across %d secret spec(s) from Key Vault",
        len(fetched),
        len(SECRET_SPECS),
    )
    return fetched


def _fetch_single_secret(client: object, secret_name: str) -> str | None:
    """Fetch one secret from Key Vault. Returns None on any failure. Never logs values."""
    try:
        secret = client.get_secret(secret_name)
        return secret.value if secret.value else None
    except Exception:
        return None


def write_secrets_file(secrets: dict[str, str]) -> None:
    """
    Write secrets as KEY=VALUE env file. File is chmod 600 (owner read-only).
    """
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        "# kairix secrets — written by vault-agent",
        f"# KV: {KV_NAME}",
        "",
    ]
    for env_var, value in sorted(secrets.items()):
        safe_value = value.replace("\n", "").replace("\r", "")
        lines.append(f"{env_var}={safe_value}")

    # By-design: vault-agent writes secrets to tmpfs-backed file (chmod 600,
    # ephemeral, not persisted to disk). Documented in SECURITY.md §3.
    content = "\n".join(lines) + "\n"
    SECRETS_FILE.write_text(content, encoding="utf-8")  # nosec: intentional secret file write
    SECRETS_FILE.chmod(0o600)
    logger.info("Wrote %d secret(s) to secrets file", len(secrets))


def signal_ready() -> None:
    """Write the readiness marker file checked by the kairix container healthcheck."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    READY_FILE.write_text("ready\n", encoding="utf-8")
    READY_FILE.chmod(0o644)
    logger.info("Ready signal written")


def main() -> None:
    if not KV_NAME:
        logger.error("KAIRIX_KV_NAME is not set. Cannot fetch secrets. Exiting.")
        sys.exit(1)

    first_run = True
    consecutive_failures = 0

    while True:
        try:
            secrets = fetch_from_keyvault()
            if secrets:
                write_secrets_file(secrets)
                consecutive_failures = 0
                if first_run:
                    signal_ready()
                    first_run = False
                    logger.info(
                        "Startup complete: %d secret(s) loaded. Refreshing every %ds.",
                        len(secrets),
                        REFRESH_INTERVAL,
                    )
            else:
                consecutive_failures += 1
                logger.error(
                    "No secrets fetched (attempt %d). Check KAIRIX_KV_NAME and Azure auth.",
                    consecutive_failures,
                )
        except Exception:
            consecutive_failures += 1
            logger.exception("Unexpected error fetching secrets (attempt %d)", consecutive_failures)

        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
