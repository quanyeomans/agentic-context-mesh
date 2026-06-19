# Installing kairix

kairix is the knowledge store an AI agent (or a team of agents and humans) reads from + writes to. Pick the install track that matches where the agent runs.

| Track | Who it's for | Needs root? |
|---|---|---|
| Linux — system install | Production servers; teams sharing one knowledge store | Yes |
| Linux — user install | A single agent on a laptop or dev box with its own knowledge store | No |
| Linux — Docker | Ephemeral hosts, VM-managed deploys, multi-tenant | No (Docker handles it) |
| macOS / Windows | A single agent on a personal machine | No |

All four end with a `kairix mcp serve` endpoint the agent can connect to. If Docker is the one you want and you just need it running, jump to [quick-start.md](quick-start.md). The rest of this page covers the pip-based install paths.

---

## On Linux — system install

For production servers + shared-knowledge-store deployments where kairix needs to run as a managed service under its own account.

```bash
pipx install Kairix-agentic-knowledge-mgt
sudo $(which kairix) init --system
sudo systemctl start kairix
```

> pipx keeps kairix in its own virtualenv and puts the `kairix` command on your PATH. A bare `pip install` fails on modern distros (Ubuntu 24.04, Debian 12+) because the system Python is externally managed (PEP 668). If you'd rather manage the virtualenv yourself, `python3 -m venv` + `pip install Kairix-agentic-knowledge-mgt` inside it works the same way. The `$(which kairix)` is needed because `sudo` resets PATH.

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
sudo kairix uninstall --system
```

Keeping data is the default. Pass `--no-keep-data` if you also want the data directory (SQLite index, vector index, documents) deleted.

---

## On Linux — user install

For an agent (or a single human) running kairix as its own private knowledge store under its own user account. No sudo, nothing system-wide.

```bash
pipx install Kairix-agentic-knowledge-mgt
kairix init --user
systemctl --user start kairix
```

> Why pipx? `pip install --user` fails with `externally-managed-environment` on modern distros (PEP 668 — Ubuntu 24.04, Debian 12+). pipx creates an isolated virtualenv per tool; a self-managed `python3 -m venv` works too.

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

Same `pipx install` + `kairix init --user` pattern as Linux user mode. Paths follow each platform's conventions. (Homebrew Python is also PEP 668 externally managed — pipx or a venv, not bare pip.)

```bash
pipx install Kairix-agentic-knowledge-mgt
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

`kairix embed` walks your configured document root and embeds every file the agent should be able to retrieve from. `kairix onboard check` validates 18 things the agent's first request needs (search, secrets, vector index, neo4j, sample query) — exits 0 when the knowledge store is ready to serve.

Then connect the agent. See [connecting-agents.md](connecting-agents.md) for Claude Desktop, OpenAI agents, LangGraph, and other clients — kairix exposes one MCP server with the same tool set for all of them.
