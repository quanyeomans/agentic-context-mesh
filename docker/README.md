# Docker Sidecar Secrets Pattern

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

| KV Secret Name                        | Env Var Written                    |
|---------------------------------------|------------------------------------|
| `azure-openai-api-key`                | `AZURE_OPENAI_API_KEY`             |
| `azure-openai-endpoint`               | `AZURE_OPENAI_ENDPOINT`            |
| `azure-openai-embedding-deployment`   | `AZURE_OPENAI_EMBED_DEPLOYMENT`    |
| `azure-openai-gpt4o-mini-deployment`  | `AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT` |
| `kairix-neo4j-password`               | `KAIRIX_NEO4J_PASSWORD`            |

## Usage

```bash
# Set your Key Vault name
export KAIRIX_KV_NAME=kv-my-vault

# Build and start all services
docker compose -f docker/docker-compose.yml up --build
```

## Local development (without Docker)

For local dev, set env vars directly or use a `.env` file — `kairix.secrets.load_secrets()`
is a no-op when `/run/secrets/kairix.env` does not exist.

## Secret resolution in Python

`kairix.secrets.get_secret(name)` resolves secrets in this order:

1. **Direct env var** — fastest path, used in tests and local dev
2. **Sidecar file** — reads `$KAIRIX_SECRETS_DIR/kairix.env` (Docker pattern)
3. **Key Vault CLI** — `az keyvault secret show` when `KAIRIX_KV_NAME` is set (VM fallback)

Raises `OSError` with a clear message if a required secret cannot be resolved.
