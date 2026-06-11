# Docker Sidecar Secrets Pattern

> **Looking for the laptop / single-VM quick start?** Use the
> `docker-compose.yml` at the repo root — it reads a plain `.env`
> (`cp .env.example .env`) and bundles neo4j. The stack in THIS directory
> is the production VM pattern with a Key Vault sidecar; the
> `/run/secrets/kairix.env` wiring lives here, not in the base file.

The `docker-compose.yml` in this directory implements a vault-agent sidecar that
fetches secrets from Azure Key Vault and makes them available to the kairix service
and neo4j database via a shared in-memory volume.

## Architecture

```
┌─────────────────────┐     tmpfs volume      ┌──────────────────────┐
│    vault-agent      │ ──/run/secrets/──────▶ │      kairix          │
│  (azure-cli image)  │   kairix.env (600)     │  (kairix:latest)     │
│  fetches from KV    │                        │  reads at startup    │
│  every 8h           │         ┌──────────────│                      │
└─────────────────────┘         ▼              └──────────────────────┘
                        ┌───────────────┐
                        │    neo4j      │
                        │  sources env  │
                        │  at entrypoint│
                        └───────────────┘
```

### How it works

1. **vault-agent** starts first. It authenticates to Azure Key Vault using `az login`
   (Managed Identity on Azure VMs, or service principal credentials via env vars).
2. It fetches the five required secrets and writes them as `KEY=VALUE` pairs to
   `/run/secrets/kairix.env` on a tmpfs volume — never written to disk.
3. The file is `chmod 600` and the volume is mounted read-only into `kairix` and `neo4j`.
4. Secrets refresh every `REFRESH_INTERVAL_SECONDS` (default: 28800 = 8 hours).
5. The compose healthcheck (`test -f /run/secrets/kairix.env`) ensures `kairix` only
   starts after the first successful fetch.

### Secrets fetched

Canonical names follow [`docs/operations/secrets-configuration.md`](../docs/operations/secrets-configuration.md)
(`kairix-<scope>-<area>-<leaf>`). The sidecar fetches the canonical KV name
first and falls back to the pre-canonical short name (e.g. `kairix-llm-api-key`)
so existing vaults keep working. Each value is written under its canonical
env var plus the legacy alias (removed with #369).

| Canonical KV Secret Name          | Env Vars Written                                        |
|-----------------------------------|---------------------------------------------------------|
| `kairix-provider-llm-api-key`     | `KAIRIX_PROVIDER_LLM_API_KEY` + `KAIRIX_LLM_API_KEY`    |
| `kairix-provider-llm-endpoint`    | `KAIRIX_PROVIDER_LLM_ENDPOINT` + `KAIRIX_LLM_ENDPOINT`  |
| `kairix-provider-llm-model`       | `KAIRIX_PROVIDER_LLM_MODEL` + `KAIRIX_LLM_MODEL`        |
| `kairix-provider-embed-api-key`   | `KAIRIX_PROVIDER_EMBED_API_KEY` + `KAIRIX_EMBED_API_KEY` |
| `kairix-provider-embed-endpoint`  | `KAIRIX_PROVIDER_EMBED_ENDPOINT` + `KAIRIX_EMBED_ENDPOINT` |
| `kairix-provider-embed-model`     | `KAIRIX_PROVIDER_EMBED_MODEL` + `KAIRIX_EMBED_MODEL`    |
| `kairix-infra-neo4j-password`     | `KAIRIX_INFRA_NEO4J_PASSWORD` + `KAIRIX_NEO4J_PASSWORD` |

## Usage

```bash
# Set your Key Vault name
export KAIRIX_KV_NAME=kv-my-vault

# Build and start all services (sidecar stack — this directory's compose file)
docker compose -f docker/docker-compose.yml up --build
```

## Local development (without Docker)

For local dev, set env vars directly or use a `.env` file
(`cp .env.example .env` at the repo root) — `kairix.secrets.load_secrets()`
is a no-op when `/run/secrets/kairix.env` does not exist.

## Secret resolution in Python

`kairix.secrets.get_secret(name)` resolves secrets in this order:

1. **Direct env var** — fastest path, used in tests and local dev
2. **Sidecar file** — reads `$KAIRIX_SECRETS_DIR/kairix.env` (Docker pattern)
3. **Key Vault CLI** — `az keyvault secret show` when `KAIRIX_KV_NAME` is set (VM fallback)

Raises `OSError` with a clear message if a required secret cannot be resolved.
