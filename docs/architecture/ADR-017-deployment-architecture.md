---
type: adr
id: ADR-017
title: Deployment architecture
status: active
date: 2026-05-18
related:
  - retrieval-boost-configuration
---

# ADR-017: Deployment Architecture

**Status:** Accepted
**Date:** 2026-04-26

---

## Current direction (2026-06)

The **deploy plane migrated to the App-bot + WIF + `azure-vm-deploy` standard.**
Release/deploy workflows (`release.yml`, `release-alpha.yml`, `release-vm-deploy.yml`)
now run as the **`three-cubes-agent` GitHub App over Workload Identity
Federation** — each job mints a short-lived installation token at runtime via
tc-pipelines' `github-app-token@v1` (Key Vault → App creds, no GitHub-stored
secret), so tags, releases, and dispatches are authored by the App.

The **VM deploy uses the canonical tc-pipelines reusable workflow
`azure-vm-deploy.yml@v1`**, invoked by `release-vm-deploy.yml` on an **alpha
prerelease** (not every merge). kairix's invocation requires the reusable
workflow to take an OS-disk snapshot before it runs the box-side apply script.
The deploy identity must therefore hold **Disk Snapshot Contributor** on the
deploy resource group. Container-level recovery is still handled by re-pinning
`KAIRIX_IMAGE_TAG` to the previous image if apply-alpha detects a failed
container health/onboard transition. The workflow also passes **empty
`smoke-units`**, because the oneshot
`kairix.service` makes `systemctl is-active` the wrong probe. Health is verified
**in-band by the box-side apply script**: `apply-alpha.sh` runs
`kairix onboard check --json` plus the reference-library regression gate after the
image flip. The box-side apply logic is the **single source** in
[`scripts/deploy/apply-alpha.sh`](../../scripts/deploy/apply-alpha.sh); the manual
fallback, when CI is unavailable, is to take a VM/database backup first and then
run that script directly on the box
(`sh apply-alpha.sh <tag>` from the compose dir). This follows the org "canonical
in tc-pipelines, don't reinvent" rule — use the shared deploy workflow and WIF
login rather than re-implementing them per repo. It satisfies the authoritative
"recovery point before apply, probe after apply" model: the VM disk snapshot is
the infrastructure recovery point; the `KAIRIX_IMAGE_TAG` re-pin is the container
rollback path; `onboard check --json` + the reflib regression status is the
post-apply probe.

**2026-07-23 deployment-resilience amendment:** alpha VM deploys now fail closed
if the reusable workflow cannot create the pre-apply OS-disk snapshot. If that
happens, fix the Azure role assignment for the deploy identity rather than
re-enabling snapshot skip.

**Customer-Zero VM compose root:** the active VM stack is selected from Docker
compose labels, not directory convention. The current OpenClaw/Kairix VM runs
from `/etc/kairix`; `/opt/kairix/app` is legacy state and must not be used as the
authoritative deploy root. `apply-alpha.sh` writes a small
`docker-compose.kairix-vm-ops.yml` overlay into the active compose directory and
stacks it after any local override. That overlay enables
`KAIRIX_WORKER_WRITES_VEC_INDEX` by default for the dogfood corpus so post-deploy
catch-up runs rebuild SQLite metadata and the USEARCH serving index together.
Operators can set `KAIRIX_WORKER_WRITES_VEC_INDEX=0` in `.env` before a very
large corpus rebuild if the worker-memory runbook says the host is undersized.

---

## Decision

**Docker Compose is the primary install path.** It provides the full experience — search, entity graph, background indexing — with no components for the user to install or configure separately. Neo4j is included in the stack and just works.

**pip install is the fallback** for environments where Docker is not available (e.g. locked-down corporate machines). Entity search is not available without Neo4j in this path.

---

## Primary Path: Docker Compose

```bash
git clone https://github.com/three-cubes/kairix && cd kairix
cp .env.example .env      # add your LLM API key
ln -s ~/my-notes ./documents
docker compose up -d
docker compose exec -it kairix kairix setup
```

**Prerequisite:** Docker Desktop (macOS/Windows) or Docker Engine (Linux). One-time admin install. Once Docker is installed, everything else runs without admin.

**What the user gets:**
- kairix MCP server (SSE on port 8080)
- kairix worker (hourly embed, entity seed)
- Neo4j (entity graph — people, companies, relationships)
- All three managed by Docker Compose — start, stop, logs, health checks

**User's documents:** Bind-mounted read-only from a user-chosen folder. Kairix never modifies them.

**Data paths (inside container):**

| Purpose | Container path | Host (Docker volume) |
|---------|---------------|---------------------|
| Documents | /data/vault (read-only) | ./documents (bind mount) |
| Database + vectors | /data/kairix/ | kairix-data volume |
| Neo4j | /data (Neo4j container) | neo4j-data volume |

**Agent connection:**

```json
{
  "mcp": {
    "servers": {
      "mcp-kairix": {
        "url": "http://localhost:8080"
      }
    }
  }
}
```

Works with OpenClaw (SSE), Claude Desktop (SSE), or any MCP-compatible agent.

---

## Fallback Path: pip install (no Docker)

```bash
pip install kairix
kairix setup
kairix search "your question"
```

No admin required. Runs as the installing user. Document permissions are automatic.

**Limitations vs Docker path:**
- No Neo4j — entity search (people, companies) not available
- No background worker — user must run `kairix embed` manually after adding documents
- No managed MCP server — user starts `kairix mcp serve` manually

**Data paths (user-level, all platforms):**

| Purpose | Linux/macOS | Windows |
|---------|-------------|---------|
| Config | ~/.config/kairix/ | %APPDATA%\kairix\ |
| Data (DB, vectors) | ~/.local/share/kairix/ | %LOCALAPPDATA%\kairix\ |
| Cache | ~/.cache/kairix/ | %LOCALAPPDATA%\kairix\cache\ |
| Reference library | ~/.local/share/kairix/reference-library/ | %LOCALAPPDATA%\kairix\reference-library\ |

---

## Server Deployment (Linux, always-on)

For production scenarios where agents connect 24/7.

**Reference shape:**

```
Service account:  kairix (system user, nologin, docker group)
Application:      /opt/kairix/app/                  (deployment-chosen)
Config:           /etc/kairix/.env                  (deployment-chosen)
Data:             /var/lib/kairix/ (Docker volumes) (deployment-chosen)
Documents:        bind mount, read-only (ACL or group read)
MCP:              SSE on 127.0.0.1:8080
Managed by:       systemd (kairix.service runs docker compose)
```

Paths shown are a reference shape; operators may relocate per their distribution's FHS conventions. The key invariants are:

- A dedicated service account with docker group membership and no login shell.
- Application code, config, and data on separate directories with appropriate ownership.
- MCP bound to loopback unless a reverse proxy with authentication fronts it.
- A process supervisor (systemd is the canonical choice on most distros) holding the lifecycle.

Requires admin to set up. Expected for infrastructure.

---

## Setup Wizard

Same wizard for all paths. Detects context (Docker vs pip, Neo4j available vs not).

```
Step 1: LLM Provider (Azure OpenAI / OpenAI / Other)
Step 2: Document Store (detect Obsidian vaults, offer reference library)
Step 3: Search Configuration (template: consulting / technical / general)
Step 4: Initial Index (scan, build FTS, embed)
```

If Neo4j detected: entity graph enabled automatically.
If not: skipped with note that entity search requires Neo4j.

---

## Least-privilege / hostile-environment deployment

kairix has different classes of users and deployment targets — including
security-hardened / "verified-secure" VMs with **read-only root
filesystems** and policy that forbids **privilege escalation** and writes
to **core system / OS locations**. The runtime is designed to run at least
privilege so these targets are first-class, not an afterthought:

- **No elevation at runtime.** Nothing the running service does requires
  `sudo` / root / `chown`. (The optional `kairix init --system` installer
  is the one privileged, operator-invoked, install-time exception — it
  writes the systemd unit under `/etc/systemd/system`; hardened deployments
  use the container or pip path instead and never run it.)
- **The base config is read-only.** The operator's `kairix.config.yaml`
  is mounted `:ro` at `/etc/kairix/kairix.config.yaml` (the image base).
  The runtime never writes to it.
- **Collected state goes to a writable, non-system, app-owned location.**
  Config the wizard/CLI collects is written to the **config overlay**
  (`KAIRIX_CONFIG_OVERLAY_PATH`, stock value
  `/var/lib/kairix/kairix.config.local.yaml` on the writable data volume);
  data, vectors, caches resolve under `kairix.paths` (`/var/lib/kairix` /
  `KAIRIX_DATA_DIR` / XDG). Readers merge base(`:ro`) + overlay(`:rw`) at
  read time via `kairix.config_layers.load_merged_mapping`, so the
  read-only base config is honoured without ever being mutated (#485/#492).
- **Graceful, not fatal.** If a write target is unexpectedly read-only,
  the surface renders an F21 affordance (e.g. the wizard-save rescue
  banner naming `KAIRIX_CONFIG_OVERLAY_PATH`), never a raw 500.

This principle is mechanically enforced by **F94** (`no_system_path_writes`):
production code in `kairix/**` may not write to a hardcoded system/OS path
(`/etc`, `/opt`, `/usr`, …); config + state writes resolve through
`kairix.paths` and the overlay instead. `/var/lib/kairix` (data dir) and
`/run` (tmpfs / secrets mount) are writable and not flagged.

---

## Consequences

- Docker Compose is the recommended path in all docs and the README
- pip is documented as "Without Docker" alternative
- Neo4j is included by default (Docker) — users never configure it manually
- Server deployment is a separate section for sysadmins
- The setup wizard adapts to whatever context it finds
