# Kairix Management Console — JTBD, Customer Success, and First-20-Minute Onboarding

**Status:** Partially implemented — the first-run guided onboarding journey (AHA-1) shipped as the web setup wizard; the steady-state console jobs (AHA-4..AHA-7, the multi-page IA) remain forward-looking design.
**Shipped:** Web setup wizard (Starlette routes under `kairix/platform/setup/web/`; orchestration in `kairix/platform/setup/wizard.py`; cloud-source OAuth in `kairix/platform/setup/source_oauth.py`), gated by the `setup_wizard_web` feature flag, mounted at `/setup` on the MCP transport. Onboarding fixes landed in PR #481 (v2026.6.11 fresh-install wave); the in-wizard capability tour + agent-connect step landed in the v2026.6.18 "Set up kairix in your browser" release.
**Companion to:** `dashboard-spec.md` (information architecture + entity model + Status home spec)
**Purpose:** Anchor the dashboard's design in the actual jobs agents + human teams hire kairix to do, define what "value delivered" means for each, and specify the **first 20 minutes** as a guided journey that produces the first "aha" before the operator has had to learn any kairix-specific vocabulary.

The previous spec answered "what does the dashboard look like in steady state?" This spec answers "what does the dashboard do to deliver value, and in what order?"

> **What is realised today vs. still-to-design.** The AHA-1 first-run journey (§5) is **shipped** as the web setup wizard — read §0 below for the as-built flow before treating the §5 step descriptions as a to-build spec. The steady-state console jobs (§6 AHA-4..AHA-7, the §7 multi-page IA, the §10 open decisions) remain **forward-looking**: there is no Status home, no Sources/Agents/Collections pages, and no adoption tray in the product yet. Don't read shipped behaviour as still-to-build, or forward-looking design as shipped.

---

## 0. What shipped (web setup wizard) vs. what remains design

The first-20-minute onboarding journey this doc specified is **shipped** as the **web setup wizard** — a Starlette + Jinja2 + HTMX app under `kairix/platform/setup/web/` (`routes.py`), gated by the `setup_wizard_web` feature flag and mounted at `/setup` on the same port as the MCP transport (no second service). The backend sits behind the `SetupService` Protocol (`kairix/platform/setup/service.py`); cloud-source OAuth lives in `kairix/platform/setup/source_oauth.py`; the multi-step orchestration is in `kairix/platform/setup/wizard.py` (these two sit one level up from `web/`, beside `service.py`).

**As-built wizard flow (7 steps, `_TOTAL_STEPS = 7`):**

`welcome → provider → key → folder → indexing → tour → connect-agent → done`, with a branch for connecting cloud sources over OAuth (`source → source/picker → source/connect → source/wait → source/saved` + the `/setup/oauth/callback` redirect).

| This doc's design | What actually shipped |
|---|---|
| §5.4 Step 1 Welcome with an "I want local-only models instead" fork to Ollama | Welcome ships, but with **no local-only fork** — `welcome.html` is a single "Get started" CTA and frames setup as "about 5 minutes". The Ollama-as-secondary-path branch was not built. |
| §5.4 Step 2 rich provider **card grid** (cost-per-500-docs, get-key deep links, "Anthropic recommended" badge, signup-time annotations) | Provider step ships as a **simple radio grid** of "bring your own key" provider cards (`provider.html`); the cost estimates, get-key deep links, and recommended badge were **not** built. |
| §5.4 Step 3 paste-key with sub-second validation | **Shipped** — `key.html` + `/setup/key/validate` HTMX validation against the provider. |
| §5.4 Step 4 folder picker + live scan; Step 5 live indexing progress | **Shipped** — `folder.html` + `/setup/folder/scan`, `indexing.html` + `/setup/indexing/progress` (HTMX-polled). |
| §5.4 Step 6 "first three generated queries" first-search screen | **Replaced** by the **capability tour** (`tour.html`, #490/#488): five real runs against the just-indexed corpus, each card naming the tool an agent calls — `search`, `prep` (context pack), `memory_write` ("remember"), `brief`, `timeline`. The legacy `/setup/first-search` path stays routable as a redirect. |
| (not in this doc) connect-an-agent step | **New shipped step** — `connect_agent.html`: shows the MCP URL, per-client connect snippets, and a "verify connection" handshake so the operator wires an MCP agent before finishing. |
| §6.1 AHA-2 "add a second source" via `kairix connect <kind>` surfaced as a wizard step | **Shipped in-wizard** — the source branch (`source_oauth.py`) runs OAuth for Slack, GitHub, Google Drive, Gmail, and Google Calendar from inside the wizard, with a unit picker for Slack channels / GitHub repos. Sign-in OAuth for Slack/GitHub/Google landed in v2026.6.18. |

**What remains forward-looking (NOT shipped):**

- The `kairix/providers/console_urls.py` deep-link registry (§5.7, §8.2) — not built; the wizard uses bring-your-own-key without per-provider get-key deep links.
- The cost estimator (§5.4 cost transparency, §8.3) and the per-provider cost cards.
- The steady-state **Status home** and the §7 multi-page IA (Sources / Agents / Collections pages, the adoption tray).
- AHA-4 (memory recall surface) through AHA-7 (invite teammate) in §6 — these are console surfaces, and the console past AHA-3 is unbuilt.
- The §10 stakeholder decisions are largely **settled by what shipped**: provider-first onboarding landed (no local default), the stack is FastAPI/Starlette + Jinja2 + HTMX + Pico.css, and the first-search step became a capability tour rather than three generated queries.

The rest of this doc is the **original design spec**, preserved as the substrate the wizard was built from. Read the §5 step descriptions as design intent, reconciled against the as-built flow in the table above.

---

## 1. Why this matters

A management console that's "complete" but doesn't get the operator to their first useful agent answer in 20 minutes is a failed product. Every kairix capability — connectors, collections, scope profiles, memory, retrieval pipelines — exists in service of a small number of jobs people hire kairix to do. If we design the dashboard around the **architecture** (the v1 spec's five domains), we get a tool that's complete and unused. If we design around the **jobs**, we get a tool people actually adopt — and the architecture surfaces as needed, hidden until it's needed.

This doc adds two layers on top of the v1 dashboard spec:

1. **A JTBD frame** — the jobs agents + humans actually have, the critical success factors that prove the job got done, and the sequence of "aha moments" that drive adoption.
2. **A first-20-minute onboarding journey** — the smallest possible path from install to a real answer from kairix on the operator's own content. No credentials, no cloud, no YAML.

The steady-state dashboard from v1 remains correct for users who already have value. This doc covers the path that gets them there.

---

## 2. Jobs to be done

Kairix has three distinct audiences, each with their own JTBD. The dashboard mostly serves the **operator**, but every feature should be traceable to a job for at least one of the three.

### 2.1 Agents (AI agents using kairix as their knowledge layer)

| # | Job | When it triggers | What "done well" looks like |
|---|---|---|---|
| A1 | "Answer this user's question with their team's actual context, not generic LLM training data." | Every user message that references team-specific knowledge | Retrieval returns the right docs/emails/messages in < 1 s, ranked by relevance, with provenance |
| A2 | "Remember what I learned about this user/team in past conversations so I don't re-ask." | Every session start | Agent's memory layer returns the relevant prior facts when queried with a topic |
| A3 | "Tell me when my model of the user/team is now wrong because new info contradicts it." | When new content lands | Contradiction surfaces in the agent's next-relevant retrieval, not silently |
| A4 | "Stay scoped — don't show me data from collections this agent shouldn't see." | Every retrieval | Scope profile enforced server-side; agent literally cannot see out-of-scope content |
| A5 | "Let me write things back to memory so future sessions know what I learned." | When the user reveals something stable about themselves or the team | Agent's write to memory persists and is retrievable next session |

### 2.2 Operators (the human setting up + maintaining kairix)

| # | Job | When it triggers | What "done well" looks like |
|---|---|---|---|
| O1 | "Get my team's distributed knowledge into one place agents can use." | First install + every new source | Operator points at a source → kairix ingests → agents can retrieve, < 30 min per source |
| O2 | "Stop re-explaining team context to AI tools every session." | Daily, every time they use an AI assistant | Their agent answers the question with team-specific context without manual priming |
| O3 | "Know what kairix is doing right now and whether it's healthy." | Anytime they notice latency, missed answers, or quiet | One screen shows ingestion progress, sync state, errors — no SSH/log grepping |
| O4 | "Give different agents different views of the same corpus." | When they want a specialist agent (legal, HR, ops) | Operator creates a scope profile → assigns to agent → agent retrieves only the scoped collections |
| O5 | "Trust the platform with my private data." | Continuous (passive) | Secrets stay in KV / file with proper permissions; audit log of who-changed-what; no surprise data leaks across scopes |
| O6 | "Recover gracefully when something breaks." | Disk full, credential rotated, source moved | Dashboard names the failure + the fix; operator follows it without docs deep-dive |

### 2.3 End users (team members whose data kairix indexes; consumers of agent answers)

| # | Job | When it triggers | What "done well" looks like |
|---|---|---|---|
| E1 | "Get useful answers from agents about my team's actual situation, not generic LLM responses." | Every interaction with an agent | Agent answers reference real team-specific facts, with traceable provenance |
| E2 | "Trust the agent won't show my private info to someone else." | Continuous (passive) | Sensitivity tiers + scope profiles enforce per-recipient visibility |
| E3 | "See what kairix knows about me / my team." | Periodically (curiosity, audit, compliance) | A read-only "what's indexed about me" view, with the ability to request removal |

The dashboard primarily serves O1–O6 directly. It indirectly serves A1–A5 (the dashboard configures the surfaces agents use). E1–E3 are mostly out of scope for the operator console, though E3 may justify a small per-user view later.

---

## 3. Critical success factors

For each JTBD, the measurable signal that the job got done:

| CSF | Metric | Target | Why it matters |
|---|---|---|---|
| **CSF-1** Time to first useful answer | Minutes from `pip install` / `docker run` to first relevant retrieval from operator's own content | ≤ 10 min (median first-time-API-key path) | Calibrated against comparable BYO-key dev tools (Cursor / Aider / Claude Code). 5 min excludes provider signup; 20 min lets us off the hook on signposting quality |
| **CSF-2** Time to second source | Minutes from first answer to first multi-source retrieval | ≤ 60 min | Single-source kairix has thin advantage over grep; multi-source is the value compounding moment |
| **CSF-3** Time to scoped agent | Minutes from second source to first scope-profile-enforced retrieval | ≤ 120 min | Proves the trust story (agents can be scoped); unlocks team-wide rollout |
| **CSF-4** Daily active retrieval | Avg retrievals per day after week 1 | ≥ 20/day | If the agent isn't asking kairix, the operator isn't getting value |
| **CSF-5** Source breadth | Active sources per deployment | ≥ 3 by day 7, ≥ 5 by day 30 | Compounding value depends on cross-source synthesis |
| **CSF-6** Recovery from failure | Operator-reported time-to-fix when something breaks | ≤ 10 min | Failures will happen; the dashboard must make recovery fast and confident |
| **CSF-7** Trust signal | Operator reports they trust the scope/sensitivity model enough to share with their team | binary, asked at day 30 | This is the gate for team-wide rollout — without it kairix stays a solo tool |

The first three CSFs are the **adoption pipeline**. Every page in the dashboard should be traceable to advancing the operator's progress along this pipeline OR to maintaining one of the steady-state CSFs (4–7).

---

## 4. The aha-moment sequence

Adoption isn't one event; it's a series of moments where the operator (or their agents) discover a new value tier. Each unlock gates further investment. Designing the dashboard to deliberately walk the operator through this sequence is what turns kairix from "configured platform" into "indispensable tool."

| # | Aha moment | What just happened | Time-from-install | Dashboard's role |
|---|---|---|---|---|
| **AHA-1** | "It found the doc I was thinking of in 1 second when grep would have taken me 5 minutes." | First retrieval on the operator's own local content | ≤ 10 min | Onboarding wizard hands them a search box on their newly-indexed corpus, primes them with 3 sample queries derived from the actual content |
| **AHA-2** | "It connected my Gmail thread with my Obsidian note about the same project." | First multi-source retrieval | ≤ 60 min (after AHA-1) | Post-AHA-1 nudge: "Add a second source for cross-source insight" → add-source wizard → re-run a query that demonstrably touches both |
| **AHA-3** | "My HR agent literally cannot see the engineering Slack." | First scope-profile-enforced retrieval | ≤ 2 h (after AHA-2) | Post-AHA-2 nudge: "Create your first scoped agent" → agent + scope wizard → side-by-side demo of same query through two scopes |
| **AHA-4** | "The agent remembered what we discussed yesterday without me re-explaining." | First memory-recall in a follow-up session | ≤ 1 day | Memory page surfaces the recall happening; small "remembered from yesterday" badge in retrieval results |
| **AHA-5** | "My teammate asked the same agent a related question and got a useful answer that synthesised our work." | First cross-user retrieval that demonstrates compounding | ≤ 1 week | Activity feed on Home shows the cross-user query; "team retrieval graph" visualisation (later release) |
| **AHA-6** | "kairix told me my old assumption was wrong because new info contradicts it." | First contradiction surface | ≤ 1 month | Contradiction notifications surface on Home; per-fact contradiction history accessible from any retrieval result |
| **AHA-7** | "I trust kairix enough to let the whole team use it." | Operator invites first teammate | ≤ 1 month | Settings page exposes "invite teammate" flow once CSF-7 trust gate signals are met (audit log clean, no leaks reported, > N successful retrievals) |

The dashboard should not just *enable* these moments — it should **deliberately produce them**. After each one is achieved, a small confirmation surface ("You just hit your first cross-source retrieval — here's what's next") both confirms the moment and points at the next.

---

## 5. The first-10-minute journey (the AHA-1 path)

> **Shipped.** This journey is realised as the web setup wizard — see §0 for the as-built 7-step flow and where it diverges from the design below (notably: no local-only welcome fork, a simple BYO-key provider grid rather than the rich cost-card grid, and a capability tour in place of the three-generated-queries first-search screen). The §5 text below is the design intent the wizard was built from.

This is the most important journey in the product. Everything else compounds on it.

The previous draft of this spec proposed a local-embedder default (no API key required). Research against comparable BYO-key dev tools (Cursor, Aider, Continue.dev, Claude Code) showed that pattern doesn't fit kairix:

1. Kairix's audience is technical operators on server / VM deployments — they already have cloud-LLM accounts and prefer "the right tool" to "the easy demo."
2. Local embedders are ~10–50× slower on CPU than batched cloud calls (10 min indexing → 30 seconds).
3. Bundling a 500 MB model in the default image hurts every operator, including those who'll use cloud providers anyway.
4. Local mode is a *fallback* (privacy-required, air-gapped, no budget) — should be an explicit choice, not the path of least resistance.

The journey below adopts the **provider-first** pattern: install → pick provider → paste API key → pick content → AHA-1. The wizard explicitly signposts where to get keys for each provider so first-time users don't have to leave the dashboard to figure it out.

### 5.1 Journey principles

- **One install command** — `pip install kairix` OR `docker run kairix/kairix`. Identical regardless of provider choice.
- **Zero YAML** — operator never opens a config file. Wizard writes all config.
- **Signposted, not hidden** — every step that requires a prerequisite (API key, folder path) tells the operator exactly where to get it, with a deep link.
- **Operator's actual content** — not a sample corpus. The whole point of AHA-1 is "see your stuff, instantly."
- **Sub-second key validation** — pasting a bad key returns an F21-shaped error in < 1 sec, never silent failure 5 minutes later.

### 5.2 What's possible in 10 minutes today (audit)

| Phase | Today | Time budget |
|---|---|---|
| Install | `docker run` or `pip install` | ~2 min (image pull) |
| Provider setup | Edit YAML / set env vars, restart worker | ~10–30 min |
| First source | Edit YAML, restart worker, tail logs | ~30 min |
| First retrieval | `kairix search "<query>"` after ingest completes | ~5 min on top of ingest |
| **Total** | | **~60 min minimum, 2 h typical** |

We're 6× over. The 10-min target requires structural changes, not just UI polish.

### 5.3 What we need to change to hit 10 min

| Change | Why | Where it lives |
|---|---|---|
| **Provider picker UI with deep links** | Operator picks Azure / OpenAI / Anthropic / Bedrock / Ollama from a card grid; each card links directly to where to get the key | New page: `/setup/provider` |
| **Sub-second key validation** | Pasting a bad key returns an F21-shaped error immediately, not after first retrieval | New use-case: `validate_provider_credentials(provider, key, endpoint)` calls the provider's models-list endpoint |
| **Folder picker, no YAML** | Operator points at `~/Documents/my-vault`, kairix ingests directly without a connector config block | New: simplified "local folder" wrapper around the existing obsidian connector |
| **Built-in search UI** | No CLI step to see the result; the dashboard has a search box that hits the retrieval pipeline directly | New page: `/onboarding/first-search` |
| **Auto-bootstrap a default agent** | Operator doesn't have to create an agent to query; a "Default" agent exists with broad scope by default | Hidden until AHA-3, then exposed |
| **Auto-run embed on ingest** | No "now run embed" step; ingest pipeline triggers embed as part of the same flow | Worker change |
| **Cost transparency** | Each provider card shows an honest cost estimate; the dashboard's Home page shows running spend | `kairix.providers.cost_estimator` |

### 5.4 The 10-minute wizard, step by step

A guided wizard at `/setup` (auto-redirect from `/` on first launch). One step per page. Each step has: a clear question, a single primary action, a "skip / do later" escape hatch where reasonable, and progress indicator.

```
Setup progress: ●●●○○○  (Step 3 of 6)
```

#### Step 1 — Welcome (00:00–00:30)

> **Welcome to kairix.**
>
> Kairix gives you instant retrieval and persistent memory across your team's knowledge — so your AI agents stop reinventing your context every conversation.
>
> Let's get you to your first useful answer in about 10 minutes. You'll need:
>
> - An API key from one of the supported LLM providers (we'll show you exactly where to get one)
> - A folder of content on this machine (notes, docs, code — anything text)
>
> [Get started →]    [I want to use local-only models instead →]

The "local-only" path forks to the Ollama setup (covered separately) — but it's the secondary path, not the default.

#### Step 2 — Pick a provider (00:30–02:00)

> **Which LLM provider do you want to use?**
>
> Kairix talks to a provider for both embedding (indexing your content) and chat (when agents synthesize answers). You can switch providers later — embeddings cache by model so the switch is cheap.

A card grid of providers. Each card:

```
┌──────────────────────────────────────────────────────────┐
│  Anthropic  (recommended for new users)                  │
│                                                          │
│  Models: claude-haiku-4-5, claude-sonnet-4-6,            │
│          claude-opus-4-7                                 │
│                                                          │
│  Embedding via Voyage AI (Anthropic partner)             │
│                                                          │
│  💰 Indexing 500 docs ≈ $0.05  ·  100 queries ≈ $0.20    │
│                                                          │
│  Don't have a key?                                       │
│  → Get one at console.anthropic.com/settings/keys        │
│    (sign-up takes ~2 min, $5 free credit)                │
│                                                          │
│                                       [Choose Anthropic] │
└──────────────────────────────────────────────────────────┘
```

The full card grid (v1):

| Provider | Models exposed | Embed model | Get-key link | Rough cost |
|---|---|---|---|---|
| Anthropic | claude-haiku/sonnet/opus | voyage-3 | `console.anthropic.com/settings/keys` | $0.05 / 500 docs |
| OpenAI | gpt-4o / gpt-4o-mini | text-embedding-3-small/large | `platform.openai.com/api-keys` | $0.02 / 500 docs |
| Azure Foundry | gpt-4o / o4-mini | text-embedding-3-large | `portal.azure.com → AI Foundry` | varies by deployment |
| Azure Legacy | gpt-4 / gpt-3.5 | text-embedding-ada-002 | `portal.azure.com → Azure OpenAI` | varies |
| AWS Bedrock | Claude / Llama / Titan | Titan / Cohere | `console.aws.amazon.com/bedrock` | varies |
| Ollama (local) | llama / mistral / qwen | nomic-embed-text | `ollama.com/download` | $0 (CPU/GPU time) |

The "recommended for new users" badge on Anthropic is because it has the fastest signup (no Azure subscription required, no AWS account, free credit on signup). This is a positioning choice we can revisit.

#### Step 3 — Paste your key (02:00–05:00; varies with whether they need to sign up)

After picking a provider, the wizard navigates to a provider-specific form:

> **Connect to Anthropic**
>
> Paste your API key. We'll validate it against the provider's API before saving.
>
> API key: [____________________________________] [Validate]
> _Format: starts with `sk-ant-`. The key is stored in your local file store, never sent anywhere except Anthropic._
>
> Don't have a key yet?
> 1. Open [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) in a new tab.
> 2. Sign up if you haven't (~2 min). New accounts get $5 free credit.
> 3. Click "Create Key", copy it, paste it here.

On clicking Validate, the wizard calls the provider's models-list endpoint:
- **Success**: Green checkmark, lists the models the key has access to, "Continue" button enabled.
- **Failure**: F21-shaped error — "fix: confirm the key starts with `sk-ant-` and was copied in full. next: try generating a new key at console.anthropic.com/settings/keys."

For Azure (which needs key + endpoint), the form has both fields. For Bedrock (which uses AWS creds), the form has access-key + secret-key + region.

#### Step 4 — Pick your content (05:00–06:00)

> **Where's some of your content?**
>
> Pick a folder on this machine. Markdown, docs, code, exports — kairix can index any text. We'll use this for your first answer; you can add more sources after.
>
> [📁 Pick a folder...] or [✏️ Type a path]
>
> _Common choices: your Obsidian vault, your `~/Documents` folder, a project repo, an export from Notion._

Live scan preview on pick:
> ✓ `/Users/op/Documents/my-vault`
> Found 432 markdown files, 89 PDFs, 12 text files. Total: ~3.2M words.
> Estimated index cost: $0.04 (~30 sec).
>
> [Use this folder →] [Pick a different one]

#### Step 5 — Indexing live (06:00–07:00, varies with corpus size)

> **Indexing your content...**
>
> ████████████░░░░░░░░  213 / 533 files  ·  ~25 sec remaining
>
> Currently processing: `meeting-notes/2026-03-12-product-sync.md`
> Cost so far: $0.02
>
> _Did you know? Kairix uses both keyword search AND semantic vector search,
> combined via reciprocal rank fusion — so it finds the right doc even
> when your query and the doc don't share exact words._

Cloud-batched embeddings make this fast — 30 seconds for a 500-file vault is typical. Educational microcopy rotates every few seconds. Cost ticks live so the operator sees what they're spending.

#### Step 6 — Your first three queries + AHA-1 confirmation (07:00–10:00)

When indexing completes:

> ✓ **Indexed 533 files in 28 seconds. Total cost: $0.04.**
>
> Here are three queries we generated from your content to get you started. Click any to try it.
>
> [What's the latest decision about <project name extracted from corpus>?]
> [Find anything related to <person mentioned in corpus>]
> [What did we discuss in <recent meeting note title>?]
>
> Or type your own:
> [____________________________________________] [Search]

Each generated query is derived by sampling the corpus's most-mentioned proper nouns / dates / project names. The first click lands on a real, relevant result from their own files. After their first useful retrieval:

> ✓ **That's it.** You just retrieved across 533 files in 800 ms.
>
> What just happened:
> - Kairix split your content into 8,212 chunks
> - Embedded each chunk into a 1024-dimensional vector via voyage-3
> - Built a hybrid BM25 + vector index
> - Your query ran against both, ranked by relevance, returned the top hits
>
> [Continue exploring →]    [Skip to the dashboard →]
>
> _Ready for more? Adding a second source (your email, Slack, etc.) is where kairix
> starts connecting things across silos. We'll walk you through it when you're ready._

### 5.5 Time budget by persona

| Persona | Time-to-AHA-1 | Notes |
|---|---|---|
| Has API key in hand | **~5 min** | Paste key (1 min) → folder (1 min) → 30s index → first query → exploration |
| First-time Anthropic / OpenAI signup | **~10 min** | Adds 3–5 min for provider signup; new-user free credit covers AHA-1 cost |
| First-time Azure Foundry user | **~20 min** | Azure subscription / deployment is the slowest first-time path. Worth doing if they're already an Azure shop |
| Ollama (local) fallback | **~30+ min** | 2–7 GB model download + slower indexing. Chosen explicitly via "I want local-only" fork |

The headline north-star is **10 min** — calibrated against the median first-time-key persona.

### 5.6 What the operator has *not* had to do

By minute 10, the operator has had a useful retrieval against their own content, and they have **not** had to:

- Read any docs
- Edit any YAML
- Run any CLI commands
- Understand "connector", "cc_pair", "collection", "scope profile", or "skill"
- Restart anything

What they *have* had to do:
- Get an API key (most operators have one already; first-time signups are ~3 min)
- Pick a folder
- Type a query

The architecture is still there — under the hood, a connector was created, a cc_pair was instantiated, a default collection was auto-composed, a default agent was bootstrapped, and a sync tick was kicked off. The dashboard exposed none of it. The operator gets the architectural vocabulary later, on demand, when they reach for the next AHA.

### 5.7 Provider console deep-link registry

The wizard's "Get your key here →" links need to be maintained as cloud consoles change URLs. Centralised in code:

```python
# kairix/providers/console_urls.py
CONSOLE_URLS: dict[str, ConsoleEntry] = {
    "anthropic": ConsoleEntry(
        get_key="https://console.anthropic.com/settings/keys",
        signup="https://console.anthropic.com/login?signup",
        signup_time_min=2,
        free_credit_usd=5,
    ),
    "openai": ConsoleEntry(
        get_key="https://platform.openai.com/api-keys",
        signup="https://platform.openai.com/signup",
        signup_time_min=3,
        free_credit_usd=0,  # no free credit since 2024
    ),
    "azure_foundry": ConsoleEntry(
        get_key="https://portal.azure.com/#blade/Microsoft_Azure_ProjectOxford/CognitiveServicesHub/AIFoundry",
        signup="https://azure.microsoft.com/en-us/free",
        signup_time_min=15,  # subscription + resource provisioning
        free_credit_usd=200,  # azure new-account credit
    ),
    # …
}
```

This registry is the single source of truth for: wizard "Get key" buttons, the per-provider setup docs, and the operator-facing error messages when a key fails validation.

---

## 6. After AHA-1: the customer success checklist

> **Mixed status.** AHA-2 (multi-source) is **partially shipped** — the setup wizard runs in-wizard OAuth for cloud sources (Slack, GitHub, Google Drive, Gmail, Google Calendar; see §0 and `source_oauth.py`), but the cross-source demo-query framing in §6.1 is design intent, not built. AHA-3 through AHA-7 (§6.2–§6.6) are **forward-looking**: they assume a steady-state console (Status home, agent detail, banners, adoption tray) that does not exist yet, so the system-nudge / banner framing below describes target behaviour, not the product today.

Each subsequent AHA has its own short wizard, triggered by either operator action (clicked "add a source") or system nudge (banner on Home: "You've used kairix daily this week. Ready to connect a second source?"). The wizards live behind the Status home from the v1 spec.

### 6.1 AHA-2 — multi-source retrieval (≤ 60 min after AHA-1)

> **Partially shipped.** The source-connect branch of the setup wizard realises steps 1–3 below: a source picker, in-wizard OAuth for Slack / GitHub / Google Drive / Gmail / Google Calendar (`source_oauth.py`, sign-in OAuth landed v2026.6.18), a unit picker for Slack channels / GitHub repos, and ingest. The cross-source **demo-query** framing in steps 4–5 (the generated query designed to surface a cross-source hit, and the source-attributed result screen) is design intent, not yet built.

**Goal:** Demonstrate retrieval that visibly spans two sources.

**Wizard shape:**
1. **Pick a second source** — same UI as v1's Add-a-source wizard, but with copy framing: "What other content would you like kairix to find?" Suggest Gmail / Obsidian / SharePoint / Slack / GitHub based on cards.
2. **Per-source setup** — for cloud sources, the wizard runs OAuth in-line (now a wizard step, not a separate `kairix connect <kind>` CLI command). For local sources, same folder picker as before.
3. **Wait for ingest + embed** — same live progress as Step 4 of AHA-1.
4. **AHA-2 prompt screen** — _generated_ query that's specifically designed to surface a cross-source result:

   > Try asking: "What did the email thread say about <project> that contradicts the doc?"
   >
   > or: "Find every mention of <person> across all my sources"

5. **AHA-2 confirmation** — the result screen highlights which source each hit came from:
   > 📧 gmail: "Re: Project Atlas timeline" — 2 weeks ago
   > 📝 obsidian: "Atlas-decisions.md" — 1 week ago
   > _Connected by: 12 shared entities, 4 overlapping dates._

### 6.2 AHA-3 — scoped agent (≤ 2 h after AHA-2)

**Goal:** Operator sees that the same query returns *different* results when run through different agent scopes.

**Wizard shape:**
1. **Why scoped agents** — single screen explaining: "Different agents (HR-bot, eng-bot, exec-bot) should see different slices of your knowledge. Let's set up your first scoped agent."
2. **Create the agent** — name, optional avatar, pick a scope:
   - "Everything" (default, broad)
   - "Just this source" (narrow to one cc_pair)
   - "Just these tags" (filter on existing sensitivity / collection labels)
3. **Side-by-side demo** — the wizard runs the same query through (a) the default broad agent and (b) the new scoped agent. Shows the diff visually:

   > Query: "What are our hiring decisions this quarter?"
   >
   > **Default agent (broad scope)** — 8 results
   > 1. exec-channel: Slack thread (2d ago) — confidential
   > 2. hr-handbook: SharePoint doc (3w ago) — internal
   > ...
   >
   > **hr-bot (scope: HR only)** — 4 results
   > 1. hr-handbook: SharePoint doc (3w ago) — internal
   > 2. hr-policies: Notion page (1mo ago) — internal
   > ...
   >
   > _hr-bot literally cannot retrieve the exec-channel result. This enforcement happens at retrieval time, not in the prompt._

4. **AHA-3 confirmation** — "You've now set up scope enforcement. Different agents see different slices. This is the foundation of trust for sharing kairix with your team."

### 6.3 AHA-4 — memory recall (≤ 1 day after AHA-3)

**Goal:** Operator's agent recalls a fact from a previous session unprompted.

**Less of a wizard, more of a surface:** The Memory page on the agent's detail view shows recent retrievals + recent memory writes. When the next session demonstrates recall, a small toast surfaces:

> 💡 **agent-alpha just used memory.**
> "User mentioned working on Project Atlas in last session" — used to retrieve Atlas-related context for the current question.
> [view memory log →]

### 6.4 AHA-5 — team retrieval (≤ 1 week)

**Goal:** Multiple humans + multiple agents all using the same kairix, with retrievals demonstrably compounding.

**Surface:** Activity feed on Status home, plus a new "Team usage" widget on Home showing:

> This week: **47 retrievals** across **3 agents** for **2 humans**
> Top sources by use: gmail (18), obsidian (15), sharepoint (14)
> [view retrieval graph →]

The retrieval graph is a deferred future-release — for AHA-5 the activity feed is enough.

### 6.5 AHA-6 — contradiction surface (≤ 1 month)

**Goal:** Kairix proactively tells the agent (and surfaces to the operator) when new content contradicts something the agent previously asserted.

**Surface:** Dashboard banner on Home when contradictions appear:
> ⚡ **3 new contradictions detected.**
> 1. Agent told alice@x.com last week that the launch date was 2026-07-15 — new email from CEO confirms 2026-08-30.
> 2. Agent's HR fact log says "alice@x.com is on the Atlas team" — new HR doc says "moved to Beta team 2 weeks ago".
> [review contradictions →]

### 6.6 AHA-7 — invite teammate (≤ 1 month, gated by trust signals)

**Goal:** Operator brings a second human onto kairix.

**Gated:** Only surfaces in the dashboard once CSF-7 trust signals are met (no unresolved security findings, audit log clean, > 50 successful retrievals, no scope leak reports).

**Surface:** Settings → Team → "Invite a teammate." Generates an OIDC-allowlisted email, sends a magic link.

---

## 7. How the v1 dashboard spec changes

> **Forward-looking.** The steady-state multi-page console described here and in `dashboard-spec.md` (Status home, Sources / Agents / Collections pages, the adoption tray) is **not shipped**. What shipped is the standalone setup wizard (§0), which owns the viewport during first-run and then hands the operator to their MCP agent — it does not transition into a steady-state console because that console does not exist yet. Treat this section as the integration plan for when the console is built.

The v1 spec (`dashboard-spec.md`) covered the steady-state console. Updates to integrate this doc:

1. **Onboarding mode is a distinct UI state**, not part of the steady-state navigation. The onboarding wizard owns the entire viewport until AHA-1 is hit, then transitions to the v1 Status home.
2. **The Status home gains a "next aha" widget** — surfaces the next-in-sequence aha moment as a recommended action ("You've used kairix daily for a week. Ready to set up scoped agents?").
3. **Audit log on the Home page** includes "aha events" — operator can see their own adoption sequence.
4. **The "Add a source" wizard is the AHA-2 driver** — it should not just add the source, it should generate the cross-source demo query.
5. **The Agent wizard is the AHA-3 driver** — it should run the side-by-side demo.
6. **A new top-level "Adoption" or "Customer success" tray** (collapsed by default) shows the operator their AHA progress: ✓ AHA-1, ✓ AHA-2, ◯ AHA-3, ◯ AHA-4, etc. Each ◯ is clickable to launch the relevant wizard.

The architecture-organised IA from v1 stays correct for users past AHA-3. The journey-organised wizards in this doc serve users between AHA-0 and AHA-3.

---

## 8. What this implies about non-dashboard parts of kairix

The 10-min path isn't purely a dashboard problem. To hit CSF-1 requires:

1. **Provider validation use-case** — `validate_provider_credentials(provider, key, endpoint) -> ValidationResult` that calls the provider's models-list endpoint and returns within 1 sec. One per provider plugin. Required for AHA-1.
2. **Provider console URL registry** (`kairix.providers.console_urls`) — single source of truth for "where to get a key" links per provider. Required for AHA-1.
3. **Cost estimator** — `estimate_index_cost(provider, corpus_stats) -> CostEstimate` and `estimate_query_cost(provider, query_count) -> CostEstimate`. Required for the wizard's transparency promise.
4. **A "local folder" connector** that wraps the obsidian-style filesystem walk without the operator needing to know the word "Obsidian." Could be the same connector with a different operator-facing label. Required for AHA-1.
5. **Auto-bootstrap on first launch** — a default cc_pair, default collection, default agent, default scope profile, all generated when the operator picks their first folder. Required for AHA-1.
6. **Live ingest progress streamed to the dashboard** (HTMX polling against a structured progress endpoint). Current worker logs to docker stdout but doesn't expose progress as a structured API. Required for AHA-1 to feel polished.
7. **Sample query generation** — small extraction pipeline that finds proper nouns / dates / titles in the corpus to seed the first-query prompts. One-shot pass at ingest end. Required for AHA-1 to feel polished.
8. **Provider-switch flow** — `kairix providers switch <new>` that updates config + re-embeds incrementally (existing cache handles chunk-level cache by `(model, dim, chunk_hash)`). Required for the wizard's "switch later if you want" promise to be honest.

Engineering items **dropped** from the previous draft:
- ~~Local embedder shipped in image (sentence-transformers)~~ — Ollama becomes a provider card the operator can pick explicitly; we don't bundle.
- ~~"Local mode" as the default code path~~ — there is no local default; provider is always explicit.

This trims ~500 MB from the image and removes the need to support sentence-transformers as a first-class provider. Net engineering scope is smaller.

---

## 9. Out of scope for this doc

- Visual design (designer's first pass, after this spec is accepted)
- Multi-tenant kairix (one operator per dashboard)
- Per-end-user views (E1–E3 above)
- The exact engineering shape of the local embedder / local folder connector (each gets its own short ADR)

---

## 10. Decision points for stakeholder review

> **Mostly settled by what shipped.** The wizard build resolved most of these in flight. Decision 1 (provider grid copy) was settled by simplification — the shipped picker is a plain BYO-key radio grid with no recommended badge and no cost cards. Decisions 2–3 (AHA sequence + adoption tray) are **moot until the steady-state console exists**, since the console past AHA-3 is unbuilt. Decision 4 (Ollama card) shipped without a dedicated local-only fork; Ollama is a provider choice, not a separate path.

The original open decisions, for the record:

1. **"Anthropic recommended for new users."** — **Not applied.** The shipped provider step is a plain BYO-key grid with no recommended badge and no cost-per-500-docs cards; the rich card grid in §5.4 was simplified away.
2. **The AHA sequence order.** — **Deferred.** A2→A3→A4 remains the design recommendation; moot until the steady-state console wizards (AHA-3+) are built.
3. **Surfacing AHA progress to the operator.** — **Deferred.** No adoption tray shipped; the wizard ends at "Finish setup" rather than transitioning into a progress-tracked console.
4. **The Ollama (local) card.** — **Partially applied.** Ollama is a selectable provider, but there is no "I want local-only" welcome fork and no on-prem framing copy; it is one card among many.

Closed decisions (confirmed by the shipped wizard except where noted):
- ~~North-star time-to-AHA-1~~ → **10 min** design target; the shipped welcome screen states "about 5 minutes" for the first-run flow.
- ~~Local embedder default~~ → **No.** Provider-first onboarding; Ollama is one provider choice among many.
- ~~Stack~~ → **Starlette + Jinja2 + HTMX + Pico.css** (the wizard mounts on the MCP transport's ASGI app rather than a standalone FastAPI service).
- ~~Auth~~ → the shipped wizard uses an **operator-token grant** (loopback skips the token; non-loopback proves the `kairix-infra-operator-token` secret via header, signed cookie, or one-time tokened URL — see `routes.py`), **not** OIDC. The OIDC-against-IdP decision was for the deferred steady-state console, not this first-run wizard.

---

## 11. Appendices

### A. Concrete kairix files / surfaces this spec depends on

Shipped (what the wizard is built on):

- `kairix/platform/setup/web/routes.py` — **shipped**; the wizard's Starlette routes + Jinja2/HTMX screens, mounted at `/setup` behind the `setup_wizard_web` flag
- `kairix/platform/setup/wizard.py` — **shipped**; multi-step wizard orchestration
- `kairix/platform/setup/service.py` — **shipped**; the `SetupService` Protocol every wizard side effect runs behind
- `kairix/platform/setup/source_oauth.py` — **shipped**; in-wizard OAuth for Slack / GitHub / Google Drive / Gmail / Google Calendar (the AHA-2 per-cloud-source step)
- `kairix/connect/cli.py` — **shipped**; the standalone `kairix connect` OAuth flow the wizard's source step now reuses below the `SetupService` boundary

Not built as designed / still deferred:

- `kairix.providers.console_urls` (§5.7) — **not built**; the wizard uses bring-your-own-key without per-provider get-key deep links
- `kairix.providers.local` — deferred; local sentence-transformers embedder (Ollama is a provider choice, not bundled)
- `kairix.connectors.local_folder` — the folder picker reuses the obsidian-style filesystem walk; no separate `local_folder` connector module
- `kairix.platform.bootstrap.first_launch` — auto-bootstrap of default cc_pair / collection / agent on first folder pick (deferred shape)
- `kairix.platform.sample_queries` — superseded: the first-search "three generated queries" screen became the capability tour (`tour.html`), which runs real tool calls rather than generating sample queries
- `dashboard-spec.md` §7 (Status home) — **forward-looking**; the steady-state console operators would land on after AHA-1 is not built

### B. Anti-patterns to avoid

- **"Configure everything, then use it."** Kairix has had this shape for too long. The dashboard exists partly to remove it.
- **"Vocabulary-led tutorials."** No screen should introduce kairix-internal vocabulary (connector / cc_pair / scope profile) before the operator has experienced the value the vocabulary enables.
- **"Empty-state dashboards."** Status home should never render with 0 sources / 0 agents / 0 retrievals to a fresh operator — they should be in the onboarding wizard instead.
- **"Hidden cost."** Cloud-LLM costs are real money. Every wizard step that triggers spend must show the estimate; the Home page must show running cost.
- **"Silent key failures."** A bad API key must surface immediately at validation time, never as a confused retrieval failure 5 minutes later.
- **"Wizard maze."** Each wizard is 3–6 steps. If a wizard grows past 6 steps, it should be split or simplified.
- **"OAuth-before-first-retrieval."** Multi-source OAuth setup (Gmail, SharePoint, Slack) is AHA-2 work. Forcing it during AHA-1 breaks the 10-min target.

### C. Claude designer prompt — onboarding wizard + status home

Paste the block below into Claude designer (or any visual-design AI tool) to produce mockups for user-testing rounds.

````
You are designing the visual mockups for kairix — a private knowledge-retrieval platform that AI agents and human teams use as their persistent memory and search layer. The target user is a technical operator (developer / IT admin / engineering manager) running kairix on a server or VM for themselves or a small team.

CONTEXT
- Stack: FastAPI + Jinja2 + HTMX + Pico.css (server-rendered, no JS framework, no build step)
- Audience: technical but time-constrained. Wants "the right tool", not "the easy demo." Already has cloud-LLM accounts.
- Brand voice: confident, honest, precise. No hype. Errors carry remediation. Cost is visible.
- North-star time-to-first-value: 10 minutes from `pip install` to first useful retrieval against the operator's own content.

VISUAL DESIGN PRINCIPLES
1. State legibility first — every screen makes platform state observable at a glance.
2. One verb per page — each screen does one thing (pick provider / paste key / pick folder / search).
3. F21 errors — every error names the fix and the next step, formatted as "Fix: ... Next: ... Then: [retry button]".
4. Cost transparency — every step that triggers spend shows the estimate before the action and the actual after.
5. Reversible by default — destructive actions require explicit confirmation with impact spelled out.
6. Hidden complexity — power users can edit raw YAML; the dashboard guides without constraining.
7. Subtle progress signalling — adoption tray shows AHA-1 ✓ AHA-2 ✓ AHA-3 ◯ AHA-4 ◯ etc., dismissible.

DESIGN TOKENS (starting point — refine)
- Base: Pico.css (classless, semantic HTML, dark/light auto)
- Accent: one color (suggest deep blue #1f3a93 or similar)
- Status colors: green (healthy), yellow (degraded), red (failed), gray (idle), blue (running), dashed gray (unprovisioned)
- Type: system stack, generous line-height, tabular numerals for cost/count displays
- Spacing: 8px grid

SCREENS TO MOCK (in priority order)

1. SETUP-1 (Welcome) — single CTA "Get started", secondary "I want local-only models instead"
2. SETUP-2 (Provider picker) — card grid: Anthropic (recommended badge), OpenAI, Azure Foundry, Azure Legacy, Bedrock, Ollama. Each card: name, models exposed, embed model, cost-per-500-docs estimate, "Get your key here →" link with signup-time-min annotation.
3. SETUP-3 (Paste API key) — form with key input + "Validate" button. On success: green check + list of model names the key can access. On failure: F21 error block.
4. SETUP-4 (Pick folder) — folder picker with live scan preview showing file counts by type + total word count + estimated index cost.
5. SETUP-5 (Indexing live) — progress bar with current file being processed, live cost tick, rotating educational microcopy about what kairix is doing.
6. SETUP-6 (First three queries + AHA-1) — three system-generated query buttons derived from the corpus, plus a free-form search box. After first successful retrieval: confirmation panel showing what just happened technically.
7. STATUS HOME — see wireframe below. Always-visible header with overall health pill, ingest rate, last embed time, operator email. Left rail navigation. Main pane: Overall status, Active now (embed runs + sync ticks), Sources list (top 5), Agents list (top 5), Attention needed (warnings), Recent activity feed.
8. SOURCES LIST (`/sources`) — table of all connectors + cc_pairs, filterable by kind / status.
9. ADD-A-SOURCE WIZARD (`/sources/add`) — kind picker → per-kind discovery → credential capture → preview → apply.
10. AGENT DETAIL (`/agents/<name>`) — memory tree browser, recent retrievals, attached collections, scope profile editor.
11. COLLECTIONS COMPOSER (`/collections/<name>`) — visual editor for which cc_pairs are in the collection, with sensitivity rules + agent-access summary.

WIREFRAME (STATUS HOME — for reference)

[Provide the Status home wireframe from docs/architecture/dashboard-spec.md §7.2 here.]

DELIVERABLES PER SCREEN
- High-fidelity desktop mockup (1440 × 900 viewport)
- One mobile / tablet mockup of the same screen (768 × 1024)
- Four state variants: loading (skeleton), empty (first-time operator), error (F21 error block), populated (real data)
- Hover / focus / disabled states for primary interactive elements
- One short "interaction note" per screen explaining what happens on click of each primary CTA

OUT OF SCOPE
- Marketing pages, landing pages, signup flows for kairix itself (this is the in-product console)
- Mobile-first layouts (desktop-first, tablet-tolerant)
- Animations beyond simple HTMX transitions (no Lottie / framer-motion)
- Branding / logo / wordmark beyond the simple "kairix" text in the header

TONE AND COPY EXAMPLES
- "Indexed 533 files in 28 seconds. Total cost: $0.04." (matter-of-fact, numbers visible)
- "github-org connector has 3 dead-lettered rows. First seen 6h ago, last 2h ago. Likely cause: a PAT scope was rotated." (specific, actionable)
- "Choose this only if you need on-prem operation." (honest about trade-offs)
- NOT: "Welcome to kairix! 🚀 Let's get started on your AI journey..." (no hype)

USER-TESTING SCENARIO TO DESIGN FOR
Imagine an operator (Sarah, software engineer at a 12-person startup) who's heard about kairix from a colleague, has an Anthropic API key from a side project, and wants to see if kairix can help her team stop re-explaining context to Claude every conversation. She runs `docker run kairix/kairix` on her laptop. From that moment, what's the shortest path to her saying "huh, that actually worked"?
````

### D. Replit prototype prompt — build the onboarding + status home scaffold

Paste the block below into Replit's agent to produce a working FastAPI + HTMX prototype that can be deployed for user testing. The prototype uses canned data — no real kairix backend required — so designers can iterate on UX without waiting for the real backend.

````
Build a FastAPI + Jinja2 + HTMX prototype of the kairix management console. The goal is a clickable prototype for user testing — canned data is fine, no real backend needed.

STACK
- Python 3.12+
- FastAPI + Uvicorn
- Jinja2 templates
- HTMX 1.9+ (via CDN, no build step)
- Pico.css 2.x (via CDN, classless semantic HTML)
- starlette SessionMiddleware for cookie-backed sessions (any random secret for the prototype)
- authlib for OIDC client — but for the prototype, stub auth with a "Sign in as test operator" button that sets a session cookie

PROJECT STRUCTURE
app/
  main.py              — FastAPI app, route registration
  routes/
    setup.py           — onboarding wizard routes (/setup/welcome, /setup/provider, /setup/key, /setup/folder, /setup/indexing, /setup/first-search)
    home.py            — Status home (/)
    sources.py         — Sources list + add-a-source wizard
    agents.py          — Agents list + detail
    collections.py     — Collections list + composer
    system.py          — System tray (worker / embed / vec-index / secrets / features / config)
    auth.py            — Stub OIDC routes (/login, /callback, /logout)
  templates/
    base.html          — Global layout: header strip, left nav, main pane
    setup/             — One template per wizard step
    home.html
    sources/           — list.html, add.html, detail.html
    agents/
    collections/
    system/
    partials/          — HTMX fragments returned by polled endpoints
  static/
    pico.min.css       — Pico.css 2.x
    kairix.css         — Brand overrides (one accent color, status colors)
    htmx.min.js        — HTMX 1.9+
  data/
    canned.py          — All canned data (providers, sources, agents, collections, status snapshots)

CANNED DATA REQUIREMENTS
- 6 providers: anthropic (recommended), openai, azure_foundry, azure_legacy, bedrock, ollama
- Each provider has: name, models (list), embed_model, cost_per_500_docs_usd, get_key_url, signup_time_min, signup_url, free_credit_usd
- 5 sources: obsidian-personal (healthy), gmail-personal (healthy), sharepoint-corp (healthy), slack-workspace (paused), github-org (degraded — 3 dead-letters)
- 2 agents: agent-alpha (broad scope, last query 4m ago), agent-beta (HR-only scope, last query 2h ago)
- 3 collections: all-knowledge (broad), hr-only (HR docs + Slack #hr), engineering (GitHub + Slack #eng + Notion eng-wiki)
- 1 active embed run: 72% done, 100% cache hits, 28 min remaining, started 4h ago
- 1 attention item: github-org dead-letters
- 5 recent activity log entries

ROUTES TO IMPLEMENT (with route → template mapping + HTMX behaviour)

GET /                      → if no session: redirect /login; if no setup completed: redirect /setup/welcome; else: home.html
GET /login                 → stub login page with "Sign in as test operator" button
POST /login                → set session cookie, redirect /
GET /logout                → clear session, redirect /login

GET /setup/welcome         → setup/welcome.html
GET /setup/provider        → setup/provider_picker.html (renders 6 provider cards from canned data)
POST /setup/provider       → form posts chosen provider, redirects /setup/key?provider=<chosen>
GET /setup/key             → setup/paste_key.html (form differs per provider: openai/anthropic = key only; azure = key + endpoint; bedrock = access-key + secret + region)
POST /setup/key/validate   → HTMX POST: simulates validation with 800ms delay; canned response (success returns model list, failure returns F21-shaped error). Returns partials/key_validation_result.html
POST /setup/key            → on full submit, store in session, redirect /setup/folder
GET /setup/folder          → setup/folder_picker.html
POST /setup/folder/scan    → HTMX POST: canned scan result (533 files, 3.2M words, $0.04 cost estimate). Returns partials/folder_scan_result.html
POST /setup/folder         → on confirm, redirect /setup/indexing
GET /setup/indexing        → setup/indexing.html with an HTMX-polled progress bar
GET /setup/indexing/progress  → returns partials/indexing_progress.html every 1s; advances canned counter; after 30s shows "complete" and redirects via HX-Redirect to /setup/first-search
GET /setup/first-search    → setup/first_search.html with 3 canned generated queries + free-form search box
POST /setup/search         → HTMX POST: returns partials/search_results.html with canned hits
GET /setup/aha-1           → setup/aha_confirmation.html

GET /                      → home.html (Status home) with HTMX polling every 5s on Active-now + header
GET /home/active           → partials/home_active.html (polled)
GET /home/sources          → partials/home_sources.html (polled every 30s)
GET /home/agents           → partials/home_agents.html (polled every 30s)
GET /home/attention        → partials/home_attention.html (polled every 30s)
GET /home/activity         → partials/home_activity.html (polled every 60s)

GET /sources               → sources/list.html
GET /sources/add           → sources/add_kind_picker.html
GET /sources/add/<kind>    → sources/add_kind_setup.html (skeleton — just shows the per-kind form fields)
GET /sources/<id>          → sources/detail.html

GET /agents                → agents/list.html
GET /agents/<name>         → agents/detail.html

GET /collections           → collections/list.html
GET /collections/<name>    → collections/detail.html

GET /system/embed          → system/embed.html (shows the canned active embed run)

DESIGN BEHAVIOURS
- Header strip: persistent across all pages once authenticated. Shows: kairix wordmark, overall health pill, "42 chunks/s ingesting" (when active), last embed completed time, operator email dropdown with Sign out option.
- Left nav: 5 items (Home / Sources / Agents / Collections / System) + Help footer. Active page highlighted.
- Empty states: every list page renders its empty state when canned data is empty — design these too.
- Loading states: every HTMX-polled endpoint returns a skeleton on first load, then real content.
- Error states: implement /setup/key/validate to randomly return an F21-shaped error 1 in 5 times so testers see both paths.

ACCEPTANCE CRITERIA
- All routes return 200 (or correct redirect) with no errors
- The full onboarding wizard can be walked through (welcome → provider → key → folder → indexing → first-search → home) without crashes
- The Status home updates live via HTMX polling without page reload
- Pico.css default styles + a single brand accent (suggest #1f3a93) are visible
- F21 error blocks render with Fix / Next / Then structure on the key-validation failure path
- Deployable to Replit's hosted environment with one click; reachable at the generated URL
- README.md at root explaining how to run locally and how the canned data can be edited

OUT OF SCOPE FOR PROTOTYPE
- Real OIDC integration (stub login button is enough)
- Real provider validation (canned 800ms-delay simulation is enough)
- Real ingestion / embedding / retrieval (canned data + canned search responses are enough)
- Mobile layout (desktop only)
- Tests (manual walk-through is the prototype's acceptance gate)

USER-TESTING PROTOCOL THIS SUPPORTS
Sarah (the persona from the designer brief) sits down with the deployed prototype. She's given the Replit URL, told "this is kairix's setup wizard — see how far you can get." She has her Anthropic key in a sticky note. We observe whether she reaches the AHA-1 confirmation screen in ≤ 10 minutes, what she says aloud at each step, and where she hesitates. The prototype's canned data should feel real enough that her hesitations are about the UX, not about disbelief that this is a real tool.
````
