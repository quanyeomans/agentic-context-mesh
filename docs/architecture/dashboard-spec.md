# Kairix Management Console — Design Spec

**Status:** Draft v0.1 (2026-06-01)
**Audience:** Visual designers (Claude designer), prototype builders (Replit), kairix engineers
**Purpose:** Define the dashboard scope, information architecture, entity model, and visual primitives so external collaborators can mock up + prototype without needing kairix-internal context. Drill-down: detailed page spec for the **Status home**.

---

## 1. Context for the cold reader

**Kairix** is a private knowledge-retrieval platform. An operator points it at sources (Obsidian vaults, Gmail mailboxes, SharePoint sites, Slack workspaces, GitHub repos, …), kairix ingests + indexes them, and one or more AI agents use kairix to retrieve relevant context for their conversations and tasks.

Today kairix is operated entirely through a CLI (~35 subcommands) and YAML config files. The CLI works but the UX has two failure modes:

1. **Operational opacity** — to answer "is the platform healthy?" the operator must SSH to the host, `docker logs` the worker, grep for errors. There's no single screen that answers "what's the state right now?"
2. **Configuration sprawl** — adding a new source means: navigate the source's developer console (GCP, Entra ID, Slack apps page, GitHub apps) → capture credentials → edit YAML in ~5 different blocks → restart the worker → tail logs to see if it worked. ~30 min for the easiest case, several hours for the hardest.

This dashboard exists to make the platform **legible** (observable state) and **approachable** (guided setup) for human operators while preserving the CLI + MCP surfaces for power users and AI agents.

## 2. Audience & deployment context

**Primary user:** Solo operator running their own kairix instance on a VM or laptop. They are technically capable (can read YAML, can SSH) but not full-time on kairix administration. They care about: "is it working?", "did the thing I asked it to do happen?", "how do I add my new SharePoint site?"

**Secondary user:** Small-team admin (2–5 people) sharing a kairix instance. Same needs as solo, plus: see what other admins changed recently.

**Deployment shape:** Single Docker container (or a small compose stack) running on a private VM or developer laptop. Dashboard is reached via browser at `http://localhost:8080/console` (default) or `http://<vm-host>:8080/console` over LAN/VPN. Not internet-exposed by default.

**Identity:** Operator already has an enterprise SSO account (Microsoft Entra, Google Workspace, or GitHub). The dashboard authenticates against the operator's existing IdP via OIDC. No kairix-managed passwords.

## 3. Design principles

1. **State legibility before action.** Every page makes the platform's state observable at a glance. No "click here to find out if it's broken." Status is always visible in the header strip.
2. **Journey-led, not feature-led.** Users come to the dashboard with a job (add a Gmail source / see why ingestion stalled / let agent-beta access the SharePoint collection). Pages map to jobs, not to architectural concepts.
3. **One verb per page.** Add, edit, retire, pause, resume — each page does one thing. Wizards span multiple pages but each step is one verb.
4. **Errors carry remediation.** Every error message names the fix and the next step (the F21 contract: `fix:`, `next:`, `run:` markers). No "Something went wrong."
5. **Read paths through the same APIs the CLI reads through.** No business logic in the dashboard layer. The dashboard is a fourth binding alongside CLI / MCP / worker — all four call the same Python use-case layer.
6. **Reversible by default.** Destructive actions (delete a collection, retire a connector, rotate a credential) require explicit confirmation with the impact spelled out ("This will remove 3,212 chunks from agent-beta's retrieval scope. This is reversible by re-attaching the connector within 7 days.").
7. **Hidden complexity, not removed complexity.** Power users can always edit raw YAML. The dashboard guides; it doesn't constrain.

## 4. Macro information architecture

The dashboard has **five top-level domains**. Persistent left-rail navigation; top-strip always shows global status + the signed-in operator.

```
┌─────────────────────────────────────────────────────────────────────┐
│  kairix    [● Healthy]  [42 chunks/s ingesting]    operator@org ▾  │  ← global header
├─────────────────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌────────────────────────────────────────────────────┐ │
│ │ 🏠 Home │ │                                                    │ │
│ │ 🔌 Sources│ │     ← page content per current route             │ │
│ │ 👥 Agents │ │                                                    │ │
│ │ 📚 Collec.│ │                                                    │ │
│ │ ⚙️ System│ │                                                    │ │
│ │         │ │                                                    │ │
│ │ ─────── │ │                                                    │ │
│ │ Help    │ │                                                    │ │
│ └─────────┘ └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.1 Domain map

| Domain | Purpose | Primary jobs |
|---|---|---|
| **Home** | Single-pane status + recent activity | "Is it healthy?" "What changed?" "What's running?" |
| **Sources** | Connectors + cc_pairs + their per-source state | "Add Gmail", "Why isn't SharePoint syncing", "Pause Slack" |
| **Agents** | Per-agent workspace, memory, scope profile | "Bootstrap agent-alpha", "Let beta see the policies collection" |
| **Collections** | Compose cc_pairs into logical collections, attach to agents | "Group all HR sources into a `hr` collection", "Scope `hr` to legal agents only" |
| **System** | Worker controls, embed runs, vec_index, secrets, features, raw config | "Trigger re-embed", "Rotate a credential", "Toggle a feature flag" |

### 4.2 Per-domain page inventory

#### Home
- `/` — **Status home** (detailed spec in §7)

#### Sources
- `/sources` — All sources list (connectors + cc_pairs in one table, filterable by kind / status)
- `/sources/add` — Add-a-source wizard (kind picker → per-kind discovery → credential capture → preview → apply)
- `/sources/<id>` — Per-source detail (last sync, error trace, ingested count, recent rows, path filters, pause/resume controls)
- `/sources/<id>/edit` — Edit the connector's config (path filters, sensitivity defaults)
- `/sources/<id>/credentials` — View canonical secret names; re-run OAuth flow to refresh tokens

#### Agents
- `/agents` — All agents list (name, scope profile, memory size, last activity)
- `/agents/add` — New agent wizard (name → memory location → scope profile → MCP endpoint setup)
- `/agents/<name>` — Per-agent detail (memory tree browser, recent retrievals, attached collections)
- `/agents/<name>/memory` — Memory browser (L0/L1 summaries, fact log, edit / pin / archive)
- `/agents/<name>/scope` — Edit which collections the agent can reach + per-collection sensitivity cap

#### Collections
- `/collections` — All collections list (name, member cc_pairs count, sensitivity profile, scope assignments)
- `/collections/add` — New collection wizard (name → member picker (cc_pairs) → sensitivity rules → preview)
- `/collections/<name>` — Per-collection detail (member cc_pairs, sensitivity rules, agents with access)
- `/collections/<name>/preview` — Sample retrieval against this collection (operator types a query, sees what an agent would see)

#### System
- `/system/worker` — Worker control panel (pause/resume, last tick, queue depth, restart)
- `/system/embed` — Embed-run viewer (current run progress, history, manual trigger, force re-embed with cache reuse)
- `/system/vec-index` — Vec-index health (vector count, last save, on-disk size, recovery actions)
- `/system/secrets` — Secret resolution view (canonical names + their backend source + masked value + rotation actions)
- `/system/features` — Feature-flag status + toggle UI (with target-retire dates per F51)
- `/system/config` — Raw config editor (YAML view + validate + diff + apply) — power-user escape hatch
- `/system/diagnostics` — One-click "collect diagnostic bundle" for support: redacted config + recent logs + version info

#### Help (left-rail footer)
- `/help` — Searchable kairix usage guide + runbook index + version + license

## 5. Entity model

Two layers: **configuration entities** (operator declares these, persist in config + KV) and **runtime entities** (kairix observes these, persist in SQLite + vec_index).

### 5.1 Configuration entities

```
                      ┌──────────────┐
                      │   Provider   │ (singleton per deployment)
                      └──────┬───────┘
                             │
                             │ (LLM / embed)
                             ▼
       ┌──────────────────────────────────────────────┐
       │              Connector                       │
       │  kind / id / name / default_sensitivity      │
       │  connector_specific_config                   │
       └──────────────┬──────────────┬────────────────┘
                      │              │
                      │ instantiates │ ingests-via
                      ▼              ▼
              ┌──────────────┐  ┌──────────────┐
              │   CC Pair    │  │   Secret     │
              │  (one per    │  │ (canonical   │
              │   instance)  │  │   name)      │
              └──────┬───────┘  └──────────────┘
                     │
                     │ member-of
                     ▼
             ┌────────────────┐
             │  Collection    │
             │  sensitivity_  │
             │   floor        │
             └───────┬────────┘
                     │
                     │ attached-to
                     ▼
             ┌────────────────┐         ┌──────────────┐
             │ Scope Profile  │─────────│   Agent      │
             │  name          │ used-by │  name        │
             │  collections[] │         │  memory_dir  │
             └────────────────┘         └──────────────┘
                     │
                     │ used-by
                     ▼
             ┌────────────────┐
             │     Skill      │
             │  task_         │
             │   collections  │
             └────────────────┘
```

| Entity | Purpose | Key fields | Operator action |
|---|---|---|---|
| **Provider** | LLM + embed plugin (one per deployment) | `name` (azure_foundry / openai / bedrock / …), endpoint, model | Set once at install |
| **Connector** | A configured instance of a source plugin | `id`, `kind`, `name`, `default_sensitivity`, `connector_specific_config` | Add/edit per source |
| **CC Pair** | Connector × credential set × scope (the actual sync target) | `id`, `connector_id`, `credential_set`, `status` (active/paused/retired), `name` | Add per real sync target |
| **Collection** | Logical group of cc_pairs the agent retrieves from | `name`, `members[].cc_pair`, `members[].sensitivity_min` | Compose to match how agents think about knowledge |
| **Scope Profile** | Which collections an agent can see, with per-collection caps | `name`, `collections[].name`, `collections[].sensitivity_max` | Govern per-agent access |
| **Agent** | A working memory + scope-profile bundle | `name`, `memory_dir`, `scope_profile_name`, `mcp_endpoint` | Add per agent |
| **Skill** | Per-task retrieval recipe | `name`, `task_collections[]`, `prompt_template` | Optional — power users only |
| **Secret** | One canonical credential name → backend lookup | `canonical_name` (kairix-…), `backend` (file/azure-kv/aws-sm/…), `last_rotated` | Captured by wizards; viewable but not editable inline |
| **Feature Flag** | Cutover toggle | `name`, `default`, `value`, `target_retire_in` | Toggle for migrations |

### 5.2 Runtime / state entities

| Entity | Purpose | Key fields | Source of truth |
|---|---|---|---|
| **Embed Run** | One pass of embedding the corpus | `id`, `started_at`, `chunks_total`, `chunks_done`, `cache_hits`, `cache_misses`, `cost_estimate`, `status` (running/complete/failed) | worker logs + SQLite `embed_runs` table |
| **Vec Index Health** | The on-disk usearch index state | `vector_count`, `expected_count`, `last_saved_at`, `on_disk_bytes`, `status` (healthy/recovering/corrupt) | `kairix.core.search.vec_index.health()` |
| **Sync Tick** | One per-cc_pair sync attempt | `cc_pair_id`, `started_at`, `finished_at`, `rows_in`, `rows_skipped`, `rows_dead_lettered`, `status` | SQLite `sync_ticks` table |
| **Dead Letter** | Failed rows per cc_pair | `cc_pair_id`, `row_uri`, `error_class`, `error_message`, `first_seen`, `retry_count` | SQLite `connector_deadletter` |
| **Worker Health** | The background worker process | `pid`, `started_at`, `last_heartbeat`, `queue_depth`, `current_task` | `kairix worker status` |

### 5.3 Cross-entity invariants (the dashboard surfaces these)

- A **CC Pair** without a captured **Secret** is "unprovisioned" — shown as a warning on Sources and Home.
- A **Collection** with zero **CC Pairs** is "empty" — agents retrieving against it get no results; warned on Collections.
- An **Agent** with a **Scope Profile** that references a non-existent **Collection** is "broken" — flagged on Home with severity high.
- A **Connector** marked active but with no **Sync Tick** in the last 24 hours is "stalled" — flagged on Home with severity medium.

## 6. Cross-cutting design primitives

### 6.1 Status badge vocabulary

A small, consistent set of status pills used everywhere (Home strip, sources list, agent list, embed runs):

| Token | Visual | Meaning |
|---|---|---|
| `healthy` | green circle + label | Everything in this scope is operating as expected |
| `running` | blue circle + label, optional spinner | Active work in progress (embed, sync tick) |
| `idle` | gray circle + label | Configured but not currently working |
| `degraded` | yellow triangle + label | Working but with elevated errors / lag |
| `failed` | red square + label | Stopped or broken; needs operator attention |
| `unprovisioned` | dashed gray + label | Configured but missing prerequisites (secrets, consent) |
| `paused` | pause icon + label | Operator-paused; safe to resume |

### 6.2 Action pattern

Every write action follows the same shape:

```
┌─ Action card ──────────────────────────────────┐
│ [Add Gmail source]              [Primary button]│
│                                                │
│ Pre-action summary: what will happen.          │
│ Expected duration: ~3 min.                     │
│ Reversible: yes, via /sources/<id>/retire.     │
└────────────────────────────────────────────────┘
```

Destructive actions add a typed-confirmation step ("type the collection name to confirm").

### 6.3 Error state pattern

```
┌─ Error ────────────────────────────────────────┐
│ ⚠ Couldn't capture Gmail tokens.               │
│                                                │
│ Reason: Google did not grant a refresh_token.  │
│                                                │
│ Fix: confirm the GCP OAuth consent screen      │
│   is in Production state (not Testing).        │
│                                                │
│ Next: GCP console → APIs & Services →          │
│   OAuth consent screen → Publish app.          │
│                                                │
│ Then: [Retry capture]                          │
└────────────────────────────────────────────────┘
```

The `Fix / Next / Then` shape is the visual form of kairix's existing F21 error contract.

### 6.4 Empty / loading / error variants

Every page renders four states:
- **Loading** — skeleton placeholders for content blocks
- **Empty** — call-to-action explaining what to add ("No sources yet. [Add your first source]")
- **Error** — F21-shaped error block + retry button
- **Populated** — the real content

### 6.5 Authentication & operator identity

| Property | Choice |
|---|---|
| Auth protocol | OIDC against operator's existing IdP (Entra / Google / GitHub) |
| Library | `authlib` |
| Session storage | starlette `SessionMiddleware`, HttpOnly + Secure + SameSite=Strict cookies, signed with server-side secret |
| Allowlist | env var `KAIRIX_ADMIN_EMAILS` (comma-separated); first-launch wizard writes this |
| CSRF | per-session token via `itsdangerous`; HTMX sends via `hx-headers` global config; all non-GET requires it |
| Default bind | `127.0.0.1:8080` only; explicit `--bind 0.0.0.0` to expose on LAN + docs warning |
| Audit log | every write action logs `{operator_email, action, target, timestamp}` to a tamper-evident append-only file in the data dir |

The dashboard never sees a kairix-managed password. Operator → IdP → kairix is the only login path.

### 6.6 Typography, spacing, color tokens

Defer to designer's first pass, but recommend Pico.css as the starting framework (classless semantic HTML, no build step, dark/light auto). Override the Pico palette with a kairix-specific token set (one accent, one warning, one error). Body font: system stack (`-apple-system, BlinkMacSystemFont, …`).

## 7. Detailed spec: Status home

The single highest-leverage page. Anchors everything else. Read-only — no writes happen here, but every block links to where writes happen.

### 7.1 Goal

In one screen, answer for the operator:
1. **Is kairix healthy overall?**
2. **What's actively running right now?**
3. **What's broken or stalled?**
4. **What changed recently?**

### 7.2 Wireframe

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  kairix [● Healthy] [42 ch/s ingesting] [last embed: 12m ago]  operator@x ▾│
├─────────────────────────────────────────────────────────────────────────────┤
│ 🏠 Home  ┌────────────────────────────────────────────────────────────────┐ │
│ 🔌 Src   │                                                                │ │
│ 👥 Agt   │  Overall:  ● Healthy                                           │ │
│ 📚 Col   │  ─────────────────────────────────────────────────────────    │ │
│ ⚙️ Sys   │                                                                │ │
│          │  Active now                                                    │ │
│          │  ┌──────────────────────────────────────────────────────────┐ │ │
│          │  │ ● Embedding   1,372,250 / 1,901,133 chunks (72%)        │ │ │
│          │  │   100% cache hits  ·  ~28 min remaining                  │ │ │
│          │  │   started 4h 12m ago by operator@x  · [open detail →]   │ │ │
│          │  └──────────────────────────────────────────────────────────┘ │ │
│          │  ┌──────────────────────────────────────────────────────────┐ │ │
│          │  │ ● Sync ticks  3 running                                  │ │ │
│          │  │   obsidian-personal, gmail-personal, sharepoint-corp     │ │ │
│          │  │   [open all sources →]                                    │ │ │
│          │  └──────────────────────────────────────────────────────────┘ │ │
│          │                                                                │ │
│          │  Sources (4 active · 1 paused · 0 failed)                     │ │
│          │  ┌──────────────────────────────────────────────────────────┐ │ │
│          │  │ obsidian-personal       ● healthy   last sync 2m ago     │ │ │
│          │  │ gmail-personal          ● healthy   last sync 8m ago     │ │ │
│          │  │ sharepoint-corp         ● healthy   last sync 1h ago     │ │ │
│          │  │ slack-workspace         ⏸ paused    by operator@x 3d ago │ │ │
│          │  │ github-org              ⚠ degraded  3 dead-lettered rows │ │ │
│          │  │                                                          │ │ │
│          │  │ [+ Add source]   [Manage all →]                          │ │ │
│          │  └──────────────────────────────────────────────────────────┘ │ │
│          │                                                                │ │
│          │  Agents (2)                                                    │ │
│          │  ┌──────────────────────────────────────────────────────────┐ │ │
│          │  │ agent-alpha    scope: ops-broad     last query 4m ago    │ │ │
│          │  │ agent-beta     scope: hr-only       last query 2h ago    │ │ │
│          │  │                                                          │ │ │
│          │  │ [+ Add agent]   [Manage all →]                           │ │ │
│          │  └──────────────────────────────────────────────────────────┘ │ │
│          │                                                                │ │
│          │  Attention needed (1)                                          │ │
│          │  ┌──────────────────────────────────────────────────────────┐ │ │
│          │  │ ⚠ github-org connector has 3 dead-lettered rows.        │ │ │
│          │  │   First seen 6h ago, last 2h ago.  Likely cause: a PAT  │ │ │
│          │  │   scope was rotated.  [open dead-letter triage →]        │ │ │
│          │  └──────────────────────────────────────────────────────────┘ │ │
│          │                                                                │ │
│          │  Recent activity                                               │ │
│          │  · 12m ago — embed run started (1.9M chunks, force=true)       │ │
│          │  · 3h ago  — slack-workspace paused by operator@x              │ │
│          │  · 8h ago  — sharepoint-corp added new drive (Curated-Content) │ │
│          │  · 1d ago  — agent-beta scope updated (-finance, +hr)          │ │
│          │  [view full activity log →]                                    │ │
│          └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Content blocks

| Block | Content | Data source | Refresh cadence |
|---|---|---|---|
| **Header strip** | Overall health, ingest rate, last embed, operator email | aggregate of all below | 5s polling via HTMX `hx-trigger="every 5s"` |
| **Overall** | One status pill + one-sentence rollup | aggregate | 5s |
| **Active now** | One row per active long-running task (embed run, sync ticks group, vec-index recovery if any) | `embed_runs` table + worker queue | 5s |
| **Sources** | Top 5 by recency + counts; click-through to /sources | sync_ticks aggregated by cc_pair | 30s |
| **Agents** | Top 5 by last-query recency + counts; click-through to /agents | agent registry + retrieval logs | 30s |
| **Attention needed** | One row per unresolved warning (dead-letters, stalls, broken scope refs) | invariant checker | 30s |
| **Recent activity** | Last 5 audit-log entries | audit log file | 60s |

### 7.4 Interactions

- Every status pill is clickable → opens the relevant detail page filtered to that state ("3 failed" → /sources?status=failed)
- Every per-source / per-agent row is clickable → opens the detail page for that entity
- "+ Add source" / "+ Add agent" → opens the relevant wizard
- HTMX polling refreshes content blocks independently; the page never reloads as a whole
- No infinite scroll — explicit "view full activity log" link goes to a paginated view

### 7.5 Empty states (first-launch operator)

When the operator has never configured anything:

```
┌────────────────────────────────────────────────────────────────────┐
│ Welcome to kairix.                                                 │
│                                                                    │
│ You have:                                                          │
│  ✓ Provider configured (azure_foundry)                             │
│  ✗ No sources yet                                                  │
│  ✗ No agents yet                                                   │
│  ✗ No collections yet                                              │
│                                                                    │
│ Recommended next step: add your first source.                      │
│ Most operators start with Obsidian or Gmail.                       │
│                                                                    │
│ [+ Add your first source]                                          │
└────────────────────────────────────────────────────────────────────┘
```

### 7.6 Error / degraded states

- If the worker process is unreachable → big banner at top: "Worker unreachable. Last heartbeat 4 min ago. [check worker status →]"
- If the vec-index is recovering → header strip shows `[● Recovering vec-index]` instead of ingest rate
- If the operator's session has expired → redirect to /login with a return-to query param

### 7.7 Page-level data shape (for Replit prototype)

The `/` route returns one JSON object (server-side rendered into HTMX partials, but the shape is canonical):

```jsonc
{
  "overall_status": "healthy",      // healthy | degraded | failed
  "header": {
    "ingest_rate_chunks_per_sec": 42,
    "last_embed_completed_at": "2026-06-01T05:00:00Z",
    "operator_email": "operator@x.com"
  },
  "active": [
    {
      "kind": "embed_run",
      "id": "run-abc123",
      "label": "Embedding",
      "progress": {"done": 1372250, "total": 1901133, "pct": 72},
      "cache_hits_pct": 100,
      "eta_seconds": 1680,
      "started_at": "2026-06-01T01:48:00Z",
      "started_by": "operator@x.com",
      "detail_url": "/system/embed/run-abc123"
    },
    {
      "kind": "sync_ticks",
      "count": 3,
      "cc_pair_names": ["obsidian-personal", "gmail-personal", "sharepoint-corp"],
      "detail_url": "/sources?status=running"
    }
  ],
  "sources": {
    "active_count": 4,
    "paused_count": 1,
    "failed_count": 0,
    "preview": [
      {"name": "obsidian-personal", "status": "healthy", "last_sync_relative": "2m ago", "detail_url": "/sources/obsidian-personal"},
      // …
    ]
  },
  "agents": {
    "count": 2,
    "preview": [
      {"name": "agent-alpha", "scope_profile": "ops-broad", "last_query_relative": "4m ago", "detail_url": "/agents/agent-alpha"},
      // …
    ]
  },
  "attention": [
    {
      "severity": "warning",
      "title": "github-org connector has 3 dead-lettered rows",
      "detail": "First seen 6h ago, last 2h ago. Likely cause: a PAT scope was rotated.",
      "action_url": "/sources/github-org/dead-letters",
      "action_label": "open dead-letter triage"
    }
  ],
  "recent_activity": [
    {"at_relative": "12m ago", "summary": "embed run started (1.9M chunks, force=true)"},
    // …
  ]
}
```

### 7.8 Performance budget

- First paint: < 200 ms after auth check (skeleton + header strip)
- Full populated render: < 500 ms (all blocks)
- HTMX poll refreshes: each block independent, < 100 ms server-side
- No N+1 queries: page composition reads one aggregate use-case (`status_home_use_case`) that fans out reads in parallel

## 8. Out of scope (this spec doesn't cover)

- Visual design tokens (palette, type scale) — designer's first pass
- Internationalisation — English-only for v1
- Mobile responsive layout — desktop-first, tablet-tolerant; phone is non-goal
- Multi-tenant / multi-instance management (one kairix per dashboard)
- Real-time push (WebSockets) — HTMX polling suffices at this scale
- The wizards' detailed specs — each wizard gets its own spec doc following this template

## 9. Glossary

- **Connector** — A source plugin (e.g. the SharePoint code). One per source kind.
- **CC Pair** — A configured instance of a connector with a specific credential set and scope (e.g. "my-team's-sharepoint-site, with the Files.Read.All creds, syncing /Curated-Content"). Many cc_pairs can share one connector.
- **Collection** — An operator-defined bundle of cc_pairs that an agent retrieves against. E.g. a `hr` collection might span SharePoint's HR site + the HR Slack channel + the HR Notion page.
- **Scope Profile** — A named bundle of collections (with optional sensitivity caps) assigned to one or more agents.
- **Agent** — A consumer of kairix retrieval. Has a name, a memory directory, a scope profile, and an MCP endpoint.
- **Sync Tick** — One per-cc_pair iteration of the sync loop. Many per day.
- **Embed Run** — One pass of (re)embedding the corpus. Triggered by ingest or by an operator.
- **CC Pair status** — `active` (syncs run), `paused` (operator-paused), `retired` (data still indexed but no more sync), `unprovisioned` (configured but missing secrets).
- **Dead Letter** — A row a connector couldn't ingest. Held with the error reason for operator triage.
- **F21 errors** — Kairix's internal convention that every error message carries `fix:`, `next:`, and (where applicable) `run:` markers naming concrete remediation. The dashboard renders these visually.

---

## Appendix A — Suggested build order (for Replit)

1. Auth scaffold (OIDC against a test IdP, session middleware, CSRF token plumbing, allowlist check)
2. **Status home** (this spec) backed by a stub use-case returning canned data
3. Wire the stub to one real use-case (`/sources` read) to prove the binding pattern
4. Sources list page (`/sources`)
5. Add-a-source wizard skeleton (kind picker → stub for each kind)
6. Per-source detail (`/sources/<id>`)
7. Iterate the rest from the page inventory in §4.2

## Appendix B — Files to read for context (kairix engineers only)

- `kairix/connect/cli.py` — the existing connect family the dashboard wraps
- `kairix.config.example.yaml` — the full configuration shape
- `docs/architecture/feature-flag-architecture.md` — how feature flags work
- `docs/architecture/ENGINEERING.md` — Protocols + Pipelines + Factories pattern
- `kairix/agents/mcp/server.py` — the MCP binding (the dashboard is a sibling binding)
