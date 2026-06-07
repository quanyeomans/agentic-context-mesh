# Installing kairix

Pick the install track that matches where you're running. All three end with a working `kairix` service you can point your agent at.

| Track | Best for | Needs root? |
|---|---|---|
| Linux — system install | Production servers, shared hosts | Yes |
| Linux — user install | Laptops, dev boxes, single-user setups | No |
| Linux — Docker | Ephemeral, VM-managed, or multi-tenant hosts | No (Docker handles it) |
| macOS / Windows | Local dev on a personal machine | No |

If you already know the Docker path and just want it running, jump to [quick-start.md](quick-start.md). This page covers the longer pip-based install in three flavours.

---

## On Linux — system install

For production servers where you want kairix running as a managed service under its own account.

```bash
pip install kairix-agentic-knowledge-mgt
sudo kairix init --system
sudo systemctl start kairix
```

What `kairix init --system` does:

- Creates a `kairix` system user and group (no shell, no home login).
- Lays down config at `/etc/kairix/` with a default `kairix.config.yaml` you can edit.
- Creates data at `/var/lib/kairix/`, cache at `/var/cache/kairix/`, and secrets at `/run/secrets/kairix/`.
- Installs a systemd unit at `/etc/systemd/system/kairix.service` running as `User=kairix`.
- Enables the unit so it survives reboots.

Re-running `sudo kairix init --system` is safe — it reports `action=unchanged` for every step that's already done.

Verify the install at any time:

```bash
sudo kairix init verify
```

Exit code 0 means every install element is in place. Failures print a one-line `remediation` string so you know what to fix.

To remove kairix but keep your indexed data:

```bash
sudo kairix uninstall --system --keep-data
```

---

## On Linux — user install

For development boxes or single-user setups where you don't want to use sudo.

```bash
pip install --user kairix-agentic-knowledge-mgt
kairix init --user
systemctl --user start kairix
```

What `kairix init --user` does:

- Lays down config at `~/.config/kairix/` (or `$XDG_CONFIG_HOME/kairix/` if you set it).
- Creates data at `~/.local/share/kairix/` and cache at `~/.cache/kairix/`.
- Installs a per-user systemd unit at `~/.config/systemd/user/kairix.service`.
- No root, no global state, no shared accounts.

System and user installs can coexist on the same host — the user-mode install lands under your `HOME` and leaves `/etc/kairix/` untouched.

---

## On Linux — Docker

For ephemeral or VM-managed deployments where you don't want kairix touching the host's package layout.

```bash
curl -O https://raw.githubusercontent.com/three-cubes/kairix/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/three-cubes/kairix/main/.env.example
cp .env.example .env   # edit and add your LLM API key
docker compose up -d
```

The container runs `kairix init --user` internally on first boot, so the inside-the-container paths follow XDG conventions. Bind-mount your documents folder into `/data/documents` — the rest is handled by the compose file.

Full Docker walkthrough — including the `.env` shape, port mapping, and indexing — lives in [quick-start.md](quick-start.md).

---

## On macOS / Windows

Same `pip install` + `kairix init --user` pattern as Linux user mode. Paths follow each platform's conventions.

```bash
pip install --user kairix-agentic-knowledge-mgt
kairix init --user
```

Where files land:

| Platform | Config | Data |
|---|---|---|
| macOS | `~/Library/Application Support/kairix/` | `~/Library/Application Support/kairix/data/` |
| Windows | `%APPDATA%\kairix\` | `%LOCALAPPDATA%\kairix\` |

systemd is Linux-only, so on macOS and Windows you start kairix yourself in a terminal:

```bash
kairix worker run        # in one terminal
kairix mcp serve         # in another
```

For long-running setups, wrap these in `launchd` (macOS) or a Windows service. The runbooks in [`docs/operations/runbooks/`](../operations/runbooks/INDEX.md) cover the patterns.

---

## After install — every track

Index your documents and run the health check:

```bash
kairix embed
kairix onboard check
```

Then point your agent at the MCP server — see [connecting-agents.md](connecting-agents.md) for Claude Desktop, OpenAI agents, LangGraph, and other clients.
