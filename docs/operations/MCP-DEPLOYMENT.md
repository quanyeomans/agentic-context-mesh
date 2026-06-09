# MCP Deployment

How to deploy the kairix MCP server in front of Claude Desktop, Claude Code, OpenClaw, or any other MCP-compatible client.

## Transport choices

Kairix supports three transports. Pick one per deployment:

| Transport | Endpoint | When to use |
|---|---|---|
| `stdio` | n/a | Claude Desktop / inline use. The kairix process is launched per-session by the MCP client; communication is via the process's stdin/stdout. |
| `http` (recommended for servers) | `POST /mcp` (streamable HTTP) and `GET/POST /sse` (legacy) on the same port | Server deployments — Claude Code over a tunnel, OpenClaw, anywhere a long-running MCP daemon makes sense. Each tool call is a normal HTTP request/response so reverse proxies and gateways treat it like any other API endpoint. |
| `sse` | `/sse` only | **Deprecated.** Kept as a `--transport=sse` alias for back-compat with existing scripts; emits a warning and acts as `http`. Migrate clients to `/mcp`. |

Streamable HTTP (the `/mcp` endpoint) is the recommended transport going forward. It's stateless per request, requires no session keep-alive, and survives gateway timeouts that historically broke `/sse` deployments. SSE remains mounted on the same port for any client that hasn't switched yet.

## Container model

The Docker image is a single unified container that runs both the MCP api process and the background worker as supervised siblings under [s6-overlay](https://github.com/just-containers/s6-overlay) v3. The container's pid 1 is the s6 supervisor; the two child processes are `kairix mcp serve --transport http` (the api) and `kairix worker run` (the worker). s6 forwards signals, restarts a crashed child on the spot, and routes each child's stdout/stderr to `docker logs` with a per-service prefix.

Operationally this means `docker compose ps` lists two services — `kairix` (api + worker together) and `neo4j` — instead of three. Memory limits are folded into the unified service: the compose default is `memory: 4g` for the kairix container (was 3g for api + 1g for worker).

The container runs as the `kairix` system user (uid `995`, gid `985`) — **not root**. Files written to bind-mounted host volumes (`/var/lib/kairix`, `/var/cache/kairix`) land owned by `kairix:kairix` on the host, matching the convention used by the systemd / pip-install path. Pre-existing host volumes that were written by an older root-owned image need a one-time `chown -R 995:985 <path>` after upgrade so the new container can read/write them.

## Run

```bash
# Server deployments — recommended
kairix mcp serve --transport http --host 127.0.0.1 --port 8182

# Inline / Claude Desktop
kairix mcp serve --transport stdio
```

Flags:

- `--transport {stdio,http,sse}` — see table above.
- `--host` (default `127.0.0.1`) — bind address. **Do not bind to `0.0.0.0` without an authenticating gateway in front; the MCP server has no built-in authentication.** For Docker / docker-compose deploys, override the host-side port mapping via `KAIRIX_MCP_BIND_HOST` in your operator-side `.env` (`KAIRIX_MCP_BIND_HOST=0.0.0.0`) — the env-var indirection means the override survives every `git pull` / image refresh, so VM-side patches don't regress on upgrade.
- `--port` (default `8080`) — listening port. Auto-detected to a free port if the default is in use; set `KAIRIX_MCP_PORT` to make a substitution permanent.
- `--no-sse` — when `--transport=http`, omit the legacy `/sse` mount and serve only `/mcp`.

## Secrets injection for the MCP server

The MCP server needs the same credentials any other kairix command does — `KAIRIX_LLM_API_KEY`, `KAIRIX_LLM_ENDPOINT`, optionally connector and Neo4j secrets. How they reach the process depends on which transport you run:

- **stdio (Claude Desktop / Cursor / Aider):** kairix is launched as a subprocess by the MCP client and inherits its environment. On macOS GUI launchers (Claude Desktop) that don't pick up your shell rc, wrap kairix in a small script that sources the secrets file before exec'ing it:

  ```bash
  cat > ~/.local/bin/kairix-mcp <<'EOF'
  #!/usr/bin/env bash
  set -a
  source ~/.config/kairix/secrets/kairix.env
  set +a
  exec ~/.venvs/kairix/bin/kairix mcp serve --transport stdio "$@"
  EOF
  chmod +x ~/.local/bin/kairix-mcp
  ```

  Then point the MCP client config at `~/.local/bin/kairix-mcp` instead of `kairix`.

- **http (server deployments — recommended for shared use):** secrets load once at service start. Use systemd's `EnvironmentFile=` (pip install) or docker-compose `env_file:` / mounted `/run/secrets/kairix.env` (Docker). Clients connect to `http://host:port/mcp` and never see credentials.

- **sse (deprecated):** same as http.

Full recipes for every install × secret-source permutation (Docker .env, systemd, Azure KV, AWS Secrets Manager, GCP Secret Manager, 1Password, ECS, Cloud Run, AKS CSI) are in [`secrets-configuration.md`](secrets-configuration.md). Run `kairix secrets verify` inside the running MCP container to confirm every required secret is loaded.

## Health

Kairix exposes two health endpoints. Use `/healthz` for liveness, `/healthz/ready` for layered readiness (v2026.5.10+).

### `/healthz` — basic liveness

```bash
curl http://127.0.0.1:8182/healthz
```

Returns `{"ready": true, "uptime_s": N}` once kairix has finished cold-starting (search pipeline construction plus a cheap probe search). HTTP deployments run this warm-up before marking the readiness gate ready, so normal agents should not see cold-start on the first user-facing tool call.

If a tool call does arrive before the gate is ready, retrieval tools return the canonical retryable envelope rather than executing a partially-warmed path:

```json
{
  "status": "retryable_not_ready",
  "error": "ColdStart",
  "error_code": "KAIRIX_COLD_START",
  "tool": "search",
  "retry_after_ms": 8000,
  "estimated_seconds_remaining": 8.0,
  "agent_instruction": "Do not answer from memory, do not use a lower-quality fallback, and do not treat this as a completed retrieval. Wait retry_after_ms, retry the same 'search' call once, then surface the cold-start blocker if it is still not ready."
}
```

Clients and agent runtimes should treat `error_code=KAIRIX_COLD_START` as a wait-and-retry state, not as a completed search failure.

### Cold-start affordance contract

KFEAT-020 hardens the cold-start path against the failure mode where agents see `fetch_failed` during the container-restart window and dismiss kairix as broken instead of retrying. The contract has three layers — operators and MCP client authors only need to know the surface visible at their layer, but they all carry the same retry hint so a client can implement a single retry loop.

> **See also:** [`runbooks/cold-start-envelope-reference.md`](runbooks/cold-start-envelope-reference.md) — operator reference with the byte-exact envelope captured from the 2026-06-06 production drill, field-by-field meaning, the local reproduce-the-drill recipe, and the link to the nightly soak test that pins the contract in place.

**Layer 1 — HTTP 503 + `Retry-After` (transport).** When the readiness gate is closed (uvicorn is bound but warm-up hasn't finished), every non-health request returns:

```
HTTP/1.1 503 Service Unavailable
Retry-After: 8
Content-Type: application/json

{
  "status": "retryable_not_ready",
  "error": "ColdStart",
  "error_code": "KAIRIX_COLD_START",
  "tool": "<request path>",
  "retry_after_ms": 8000,
  "estimated_seconds_remaining": 8.0,
  "agent_instruction": "Do not answer from memory, do not use a lower-quality fallback, and do not treat this as a completed retrieval. Wait retry_after_ms, retry the same call once, then surface the cold-start blocker if it is still not ready."
}
```

This fixes the gap where MCP clients saw `fetch_failed` (a transport-level fault) during the bind-but-not-warm window. The standard `Retry-After` header lets well-behaved HTTP clients retry without reading the body; the JSON body carries the same retry hint for clients that want structured handling.

Health probes (`/healthz`, `/healthz/ready`) bypass the gate so load balancers always get an answer.

**Layer 2 — application-layer cold-start envelope.** If warm-up has completed and the request reaches the MCP router, retrieval tools (`search`, `entity`, `prep`, `timeline`, `research`, `contradict`, `brief`, `bootstrap`, `entity_suggest`, `entity_validate`, `ingest_chat`, `facts_about`) check the per-process readiness gate via the `@warm_gate` decorator. While the in-process gate is closed they return the same envelope shape as Layer 1 (defined in `kairix/agents/mcp/cold_start.py`), but as a 200 OK MCP tool response body — because the MCP protocol layer doesn't have a native retry-after channel. Diagnostic and static tools (`usage_guide`, `onboard_check`, `worker_status`, `warm`, `capabilities`, probes) stay ungated so operators can read them to diagnose the cold state.

**Expected MCP-client behaviour.**

1. On Layer 1 (HTTP 503): honour the `Retry-After` header. Wait the requested seconds, retry the same call once. If still 503, surface "kairix warming" to the user — not "kairix broken".
2. On Layer 2 (MCP tool envelope with `error_code=KAIRIX_COLD_START`): parse the envelope, wait `retry_after_ms`, retry the same call once. If the second call also returns `KAIRIX_COLD_START`, surface "kairix warming" — not "kairix returned no results".
3. Either signal is **always retryable** — never a permanent failure. Never substitute a memory-based answer for a `KAIRIX_COLD_START` response.

**Layer 3 — structured startup logs.** Each MCP HTTP-transport process emits three structured log events on the dedicated `kairix.mcp.startup` logger so operators can pivot on container-restart frequency in their log analytics layer:

| Event | When | Fields |
|---|---|---|
| `event=mcp_process_started` | Once at the top of the HTTP-transport branch, before warm-up runs. | `pid`, `host`, `port`, `python_version`, `kairix_version`, `previous_warm_age_s` (None on first start; otherwise seconds since the previous warm flag was written, so operators can tell whether the just-killed previous process was warm at death). |
| `event=mcp_warm_started` | When `warm_retrieval_stack_fn()` returns `ready=True`. | `pid`, `elapsed_ms` (from the warm envelope). |
| `event=mcp_warm_failed` | When `warm_retrieval_stack_fn()` returns `ready=False`. | `pid`, `warm_result` (full warm envelope as a JSON string, including the failing step). |

All three are `INFO`-level on the dedicated `kairix.mcp.startup` logger so operators can filter without picking up unrelated kairix log volume. Format is grep-friendly `event=<name> key=value` so plain `docker logs <container> | grep event=mcp_` works without a log shipper.

**Operator query recipe** (for any log analytics that ingests `docker logs`):

```
# count restarts in a window:
filter: logger == "kairix.mcp.startup" AND message contains "event=mcp_process_started"
pivot:  count() by hour

# correlate restart cadence with warm failures:
filter: logger == "kairix.mcp.startup" AND message contains "event=mcp_warm_failed"
pivot:  count() by hour, extract warm_result.steps[].name as failing_step

# "was the previous process warm at death?" — extract previous_warm_age_s:
filter: logger == "kairix.mcp.startup" AND message contains "event=mcp_process_started"
extract: previous_warm_age_s as age
pivot: histogram(age) — null bucket is fresh container, non-null is restart-while-warmed
```

A healthy deployment should show `mcp_process_started` no more than 1/day after Part 1's healthcheck fix; spikes correlate with operator-initiated restarts. A spike in `mcp_warm_failed` points at a warm-up dependency (build_search_pipeline failure → sqlite path / schema issue; probe_search failure → vector index missing) rather than a transient outage.

### `/healthz/ready` — layered readiness

```bash
curl http://127.0.0.1:8182/healthz/ready
```

Returns granular capability detail so a load balancer can distinguish "process up but degraded" from "fully operational":

```json
{
  "live": true,
  "ready": false,
  "uptime_s": 14,
  "checks": {
    "secrets_loaded": false,
    "vector_search_capable": false,
    "bm25_search_capable": true,
    "detail": {
      "secrets_loaded": "KAIRIX_LLM_API_KEY missing",
      "vector_search_capable": "embed credentials unavailable"
    }
  }
}
```

`ready` is the boolean to act on. The capability flags use the suffixes `_capable` (functional) and `_loaded` (configured). The `detail` map carries an actionable failure reason for any False capability. HTTP status is always 200 — load-balancer probes should treat the JSON body as the gate, not the status code.

Resolves the #167 gap where `/healthz` reported `ready=true` while vector search was silently broken because `/run/secrets/kairix.env` had never been hydrated after a reboot.

## Error envelope

Every tool handler is wrapped with `wrap_tool_errors`. Retrieval tools also have a readiness guard: when the HTTP readiness gate is closed, they return `KAIRIX_COLD_START` and do not enter the underlying search/bootstrap/research path. Any exception escaping a handler becomes a structured response:

```json
{"error": "<ExceptionClass>: <message>"}
```

Exception class names are preserved so observability can group by error type. There is no path through which a tool exception reaches FastMCP's generic `-32602 Invalid request parameters` mapper — clients that observe `-32602` are looking at a transport-level (parameter-validation) failure, not a tool-level one.

## Gateway routing

If a reverse proxy or zero-trust gateway sits in front of kairix, route the following paths through to the kairix container:

```
/mcp     → 127.0.0.1:8182 (POST + GET)
/sse     → 127.0.0.1:8182 (GET, legacy)
/healthz → 127.0.0.1:8182 (GET)
```

For Caddy:

```caddyfile
mcp.example.com {
    reverse_proxy /mcp* 127.0.0.1:8182
    reverse_proxy /sse* 127.0.0.1:8182
    reverse_proxy /healthz 127.0.0.1:8182
}
```

For Cloudflare Access tunneling, the `/mcp` endpoint is a normal POST endpoint — no SSE-specific config (idle timeouts, buffering disable) is required. SSE callers do still need streaming-safe routing if they're not migrating off `/sse` immediately.

## Observability

Each tool call writes one JSON line to the search log. Default location:

- Docker: `/data/kairix/logs/search.jsonl`
- Non-Docker: `~/.cache/kairix/logs/search.jsonl`

Schema:

```json
{
  "ts": 1709553600,
  "query_hash": "12-hex-chars",
  "intent": "semantic",
  "agent": "alpha",
  "scope": "shared+agent",
  "collections_searched": ["docs", "alpha-memory"],
  "bm25_count": 5,
  "vec_count": 5,
  "fused_count": 8,
  "vec_failed": false,
  "fallback_used": false,
  "total_tokens": 1834,
  "latency_ms": 142.7
}
```

Watch the `vec_failed` rate and the `intent` distribution as a per-deployment health signal. A spike in `vec_failed` typically indicates the sqlite-vec extension isn't loading; a spike in `latency_ms` typically points at Neo4j or the embedding service. The architecture doc `agent-memory-architecture-recommendation-2026-04-16.md` discusses how this log feeds into the multi-agent quality loop.

## Scope and the agent registry

For `scope=all-agents` and `scope=everything` to work, `kairix.config.yaml` must declare its agents:

```yaml
collections:
  shared:
    - name: docs
      path: docs
  agent_pattern: "{agent}-memory"

agents:
  - name: alpha
    write_path: agents/alpha/memory
  - name: beta
    write_path: agents/beta/memory
  - name: gamma
    read_only: true
```

Validate before deploying:

```bash
kairix config validate
```

This catches duplicate agent names, overlapping `write_path` values (a write-isolation hazard), unknown `retrieval_overrides` keys (silent typos that would otherwise be invisible), and `agent_pattern` strings that omit the `{agent}` placeholder. Wire it into the CI pre-deploy step.

## Wiring the kairix-memory-prompt plugin

The kairix MCP server exposes tools an agent *can* call. For an agent to be **oriented at session start** — to arrive with its role, current `Board.md`, recent memory, and active goals already in its system prompt — openclaw also needs to load the `kairix-memory-prompt` plugin. Without it, agents start each session context-blind and react to user prompts instead of orienting themselves.

The plugin ships with kairix (#246 W5) and lives in the container image at:

```
/opt/kairix/plugins/openclaw/memory-prompt/
├── plugin.py        # openclaw entry — calls kairix bootstrap <agent>, appends stdout to system prompt
├── plugin.json      # openclaw manifest (declares name=kairix-memory-prompt, append-only injection)
└── README.md        # operator-facing details + the openclaw plugin API assumptions
```

For non-Docker installs the same files land under `<site-packages>/kairix/plugins/openclaw/memory-prompt/`. The container image symlinks the canonical `/opt/kairix/plugins/openclaw` path at build time so admins paste a stable path into openclaw config regardless of which Python minor version site-packages lives under.

### openclaw config snippet

Paste into your openclaw config (`~/.openclaw/openclaw.json` for per-user, `/etc/openclaw/openclaw.json` on the VM image):

```json
{
  "plugins": {
    "load": {
      "paths": ["/opt/kairix/plugins/openclaw"]
    },
    "allow": ["kairix-memory-prompt"],
    "entries": {
      "kairix-memory-prompt": {
        "hooks": {
          "allowPromptInjection": true
        }
      }
    }
  }
}
```

All three keys are required: `plugins.load.paths` tells openclaw where to discover plugins, `plugins.allow` is the explicit allowlist (defence in depth against accidental loads), and `plugins.entries.kairix-memory-prompt.hooks.allowPromptInjection` grants the plugin permission to call `appendSystemContext`. Without that last key, openclaw discovers the plugin but refuses to let it modify the system prompt — which is the failure mode the original incident exposed.

### Verifying it loaded

After restarting openclaw, look at the startup log for a line like:

```
[openclaw] loaded plugin: kairix-memory-prompt (hook: onSessionStart)
```

If that line is missing, the plugin did not load — re-check `plugins.allow` for the literal string `kairix-memory-prompt` and confirm `/opt/kairix/plugins/openclaw/memory-prompt/plugin.json` exists on disk.

If the plugin loaded but the bootstrap envelope is missing from agent sessions, the runtime probably cannot find the `kairix` CLI. The plugin shells out to `kairix bootstrap <agent>` with a 5-second timeout; if the binary is not on the openclaw user's `$PATH` the plugin falls back to a short degraded message (`[kairix bootstrap unavailable — ask your admin to run kairix onboard check]`) and the session still starts. Fix by adding the kairix install dir to PATH for the openclaw service unit.

### Failure contract — degraded != broken

The plugin **never blocks session start**. On every failure path — missing binary, non-zero exit, timeout, blank agent name, empty stdout — it appends the fallback string above and returns normally. This matches the #246 affordance contract: the agent reads the fallback in its system prompt, knows kairix orientation is unavailable, and surfaces that to the user instead of silently failing. Full failure-mode notes are in `kairix/plugins/openclaw/memory-prompt/README.md`.

## Running eval from the deployed container

The Plan B-parity eval suite runner (`kairix eval`) ships in the image so operators can run quality regressions against a deployed container without rebuilding. Reference corpora and perf budgets are baked in at stable paths.

### Stable paths inside the image

| Path | Contents | Env var |
|------|----------|---------|
| `/opt/kairix/reference-library/conversations/` | five engagement corpora (engagement-alpha … engagement-epsilon), each with sessions + ground-truth queries | `KAIRIX_EVAL_CORPORA_ROOT` |
| `/opt/kairix/suites/perf/budgets.json` | per-capability latency budgets consumed by `kairix probe-config --perf` | `KAIRIX_PERF_BUDGETS` |
| `/opt/kairix/suites/` | top-level eval suite directory (reflib-gold-v3.yaml etc.) | (none — fixed path) |

Both env vars are exported in the image as documented stable references. The eval CLI does not read them today; operators templating commands should point at the literal paths above and treat the env vars as the durable contract for "where the corpora live inside the container".

### `docker exec` examples

Run a single engagement corpus against the kairix-native backend, JSON output:

```bash
docker exec <container> kairix eval \
    /opt/kairix/reference-library/conversations/engagement-alpha \
    --json
```

Compare both metrics (query pass rate + extractor F1) and check against a pinned baseline:

```bash
docker exec <container> kairix eval \
    /opt/kairix/reference-library/conversations/engagement-alpha \
    --metric both \
    --regression-against /opt/kairix/reference-library/conversations/expected/engagement-alpha
```

Cross-backend comparison (kairix-native vs mem0):

```bash
for backend in kairix-native mem0; do
    docker exec <container> kairix eval \
        /opt/kairix/reference-library/conversations/engagement-beta \
        --backend "$backend" --json > "/tmp/eval-$backend.json"
done
```

The `--regression-against` flag exits non-zero if the run regresses by more than 2pp against the pinned baseline — wire it into your nightly cron to catch quality drift between releases.

### Legacy `eval` shortcut renamed

The pre-Plan-B `eval` entrypoint mode (which ran `kairix embed && kairix benchmark run --suite reflib-gold-v3.yaml`) is now `benchmark-reflib`. The rename frees the `eval` arg so it dispatches to the new `kairix eval` suite runner via the entrypoint's pass-through case. Operators with cron entries that ran `docker run kairix eval` should switch to either:

- `docker run kairix benchmark-reflib` — same legacy reference-library quality benchmark, or
- `docker run kairix eval /opt/kairix/reference-library/conversations/<corpus>` — the new conversation-eval suite runner.
