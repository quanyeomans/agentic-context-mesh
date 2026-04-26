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

Secrets fetched (KV secret name → env var written to file):
  azure-openai-api-key               → AZURE_OPENAI_API_KEY
  azure-openai-endpoint              → AZURE_OPENAI_ENDPOINT
  azure-openai-embedding-deployment  → AZURE_OPENAI_EMBED_DEPLOYMENT
  azure-openai-gpt4o-mini-deployment → AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT
  kairix-neo4j-password              → KAIRIX_NEO4J_PASSWORD
"""

import logging
import os
import sys
import time
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

# Azure Key Vault secret name → env var name
SECRET_MAP: dict[str, str] = {
    "azure-openai-api-key": "AZURE_OPENAI_API_KEY",
    "azure-openai-endpoint": "AZURE_OPENAI_ENDPOINT",
    "azure-openai-embedding-deployment": "AZURE_OPENAI_EMBED_DEPLOYMENT",
    "azure-openai-gpt4o-mini-deployment": "AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT",
    "kairix-neo4j-password": "KAIRIX_NEO4J_PASSWORD",
}


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

    fetched: dict[str, str] = {}
    for secret_name, env_var in SECRET_MAP.items():
        try:
            secret = client.get_secret(secret_name)
            if secret.value:
                fetched[env_var] = secret.value
                logger.info("Fetched: %s", secret_name)  # lgtm[py/clear-text-logging-sensitive-data] — logs KV secret name only, not value
            else:
                logger.warning("Secret %s has empty value — skipping", secret_name)  # lgtm[py/clear-text-logging-sensitive-data] — logs KV secret name only, not value
        except Exception as exc:
            logger.warning("Failed to fetch %s", secret_name)  # lgtm[py/clear-text-logging-sensitive-data] — logs KV secret name only, not value

    return fetched


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
        # Guard against values containing newlines (shouldn't happen with API keys)
        safe_value = value.replace("\n", "").replace("\r", "")
        lines.append(f"{env_var}={safe_value}")

    SECRETS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")  # lgtm[py/clear-text-storage-of-sensitive-data] — intentional sidecar pattern: vault-agent writes secrets to tmpfs /run/secrets/ (chmod 600, ephemeral)
    SECRETS_FILE.chmod(0o600)
    logger.info("Wrote %d secret(s) to %s", len(secrets), SECRETS_FILE)  # lgtm[py/clear-text-logging-sensitive-data] — logs count and file path, not secret values


def signal_ready() -> None:
    """Write the readiness marker file checked by the kairix container healthcheck."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    READY_FILE.write_text("ready\n", encoding="utf-8")
    READY_FILE.chmod(0o644)
    logger.info("Ready signal written to %s", READY_FILE)


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
        except Exception as exc:
            consecutive_failures += 1
            logger.error("Unexpected error fetching secrets (attempt %d): %s", consecutive_failures, exc)

        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
