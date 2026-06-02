# Topology_v2 Cutover — Execution Plan

**Status:** Ready for execution (2026-06-02)
**Tracks:** #373 (cutover), #374 (legacy reap), #381 (wildcard parse-order bug)
**Builds on:** `collection-structure-design.md`, `collection-v2-implementation-plan.md`
**Production state today:** kairix v2026.6.2a1 deployed; minimal topology_v2 scaffold valid; all flags OFF; legacy resolver still drives retrieval

## Critical insight from the v2026.6.2a1 deploy

The resolver flag (`topology_v2_collection_resolver`) is the **load-bearing flip** for the cutover. Per-connector flags (`topology_v2_obsidian`, `topology_v2_sharepoint`, etc.) control the write path, not the read path.

The link from a topology_v2 `collection` to actual doc rows is the **`collection.name` ↔ `documents.collection` column match**. Connectors today write `documents.collection = <connector_name>` (e.g. `'sharepoint'`, `'obsidian'`, `'slack'`). As long as the v2 `topology_v2.collections[].name` matches what the connector writes, the v2 resolver finds the docs.

This means:
- **No connector code changes needed for the cutover** — legacy write path stays
- **The cutover is just: declare 7 cc_pairs + collections + scope_profiles, flip the resolver flag**
- **Per-connector flags can stay OFF indefinitely** — they're for a future migration to v2-native ingest, not for the read-path cutover

This shortens the cutover from ~1 week of code work to ~half a day of config + the soak.

## Pre-requisites (must land before flipping)

### P1 — Fix #381 wildcard parse-order bug

**Issue:** `_parse_scope_entry` requires `actor_id` before `_expand_wildcard_profiles` runs, so `applies_to: ["*"]` fails parsing.

**Impact:** Without the fix, the full v2 scope_profiles config needs ~78 entries (13 collections × 6 agents) instead of ~13 with a wildcard baseline. Verbose but works.

**Recommendation:** Fix #381 FIRST. Small code change (~50 LOC + tests). Makes the cutover config readable.

**Fallback:** if #381 takes longer than expected, ship the cutover with explicit per-agent entries; refactor to wildcard later.

### P2 — Verify the eval suite runs cleanly

Confirm `kairix benchmark run --suite <suite>` succeeds today against production. The cutover protocol gates on eval ±2pp, so we need the eval suite working before the flip.

**Check:** `kairix benchmark list` should show at least one suite. Run it once to baseline.

### P3 — Confirm sample-journey queries

The `capture_baseline.py` sample-journey surface depends on `cutover.sample_queries` in `kairix.config.yaml`. The v0.2 capture_baseline ships defaults but they need to be representative of real operator queries. Should be 5–10 queries covering different sources (mailbox lookups, calendar lookups, project searches, etc.) so the diff has signal.

## Phase 1 — Author the full topology_v2 config (~1 hour)

Extend the deployed minimal scaffold to cover all 7 sources + 6 agent-memory collections per the design doc.

### 1.1 — Add the 7 connectors as topology_v2 cc_pairs

```yaml
topology_v2:
  connectors:
    - id: obsidian-personal-conn
      kind: obsidian
      name: "Obsidian personal vault"
      default_sensitivity: internal
      connector_specific_config:
        vault_root: /data/documents
    - id: sharepoint-agent-exchange-conn   # already present
      kind: sharepoint
      ...
    - id: m365-calendar-conn
      kind: m365_calendar
      name: "M365 personal calendar"
      default_sensitivity: personal
      connector_specific_config:
        user_id: <UPN>
    - id: m365-email-headers-conn
      kind: m365_email_headers
      name: "M365 personal email headers"
      default_sensitivity: personal
      connector_specific_config:
        user_principal_name: <UPN>
    - id: slack-master-conn
      kind: slack
      name: "Slack — master via shape's tokens"
      default_sensitivity: personal
      connector_specific_config: {}
    - id: github-conn
      kind: github
      name: "GitHub — 3 active repos"
      default_sensitivity: client-confidential
      connector_specific_config:
        repos_allowlist: ["<org>/<repo-1>", "<org>/<repo-2>", "<org>/<repo-3>"]
    - id: reflib-conn
      kind: reflib
      name: "Reference library"
      default_sensitivity: public

  credentials:
    - id: m365-oauth
      kind: oauth2_client_credentials
      secret_name: connector-m365
      admin_public: true
    - id: github-pat
      kind: bearer_token
      secret_name: connector-github
      admin_public: true
    - id: slack-bot
      kind: bearer_token
      secret_name: connector-slack
      admin_public: true

  cc_pairs:
    - id: cc-obsidian-personal
      name: "obsidian-personal"
      connector: obsidian-personal-conn
      credential: null
      access_type: PRIVATE
    - id: cc-sharepoint-agent-exchange      # already present
      ...
    - id: cc-m365-calendar
      name: "m365-calendar"
      connector: m365-calendar-conn
      credential: m365-oauth
      access_type: PRIVATE
    - id: cc-m365-email
      name: "m365-email-headers"
      connector: m365-email-headers-conn
      credential: m365-oauth
      access_type: PRIVATE
    - id: cc-slack-master
      name: "slack-master"
      connector: slack-master-conn
      credential: slack-bot
      access_type: PRIVATE
    - id: cc-github-3repos
      name: "github-3repos"
      connector: github-conn
      credential: github-pat
      access_type: PUBLIC
    - id: cc-reflib
      name: "reference-library"
      connector: reflib-conn
      credential: null
      access_type: PUBLIC
```

### 1.2 — Define the 13 collections

```yaml
  collections:
    # In-default (7) — every agent's broad search returns these
    - name: sharepoint
      sources: [{cc_pair: cc-sharepoint-agent-exchange}]
    - name: obsidian
      sources: [{cc_pair: cc-obsidian-personal}]
    - name: slack
      sources: [{cc_pair: cc-slack-master}]
    - name: m365_email_headers   # name matches what the connector writes
      sources: [{cc_pair: cc-m365-email}]
    - name: m365_calendar
      sources: [{cc_pair: cc-m365-calendar}]
    - name: github
      sources: [{cc_pair: cc-github-3repos}]

    # Opt-in (1) — only retrieved when explicitly named
    - name: reflib
      sources: [{cc_pair: cc-reflib}]

    # Per-agent memory (6) — sourced from obsidian with path_filter
    - name: shape-memory
      sources:
        - cc_pair: cc-obsidian-personal
          path_filter: "04-Agent-Knowledge/shape/**"
    - name: builder-memory
      sources:
        - cc_pair: cc-obsidian-personal
          path_filter: "04-Agent-Knowledge/builder/**"
    # ... × 4 more agents
```

**Critical:** the `collection.name` must match the value the connector writes into `documents.collection`. Verify with:

```sql
SELECT DISTINCT collection, COUNT(*) FROM documents GROUP BY collection ORDER BY 2 DESC;
```

Today's audit (from earlier in this session):
- `sharepoint`, `default` (the leak), `obsidian`, `reference-library`, `slack` — match
- Legacy path-based (`home`, `projects`, `areas`, etc.) — irrelevant after cutover (won't be in v2 config)
- M365 calendar / email — verify what name the connector writes (likely `m365_calendar` / `m365_email_headers` matching the connector kind)

If a connector writes a different name than what we declare, the collection will be empty after the flip → silent retrieval gap. Fix at the connector code level OR rename the v2 collection to match.

### 1.3 — Define scope_profiles (assuming #381 fixed)

```yaml
  scope_profiles:
    # Shared default — every agent inherits via wildcard
    - name: agent-default
      actor_kind: agent
      applies_to: ["*"]
      entries:
        - {collection_name: sharepoint,           mode: read, default_in_scope: true}
        - {collection_name: obsidian,             mode: read, default_in_scope: true}
        - {collection_name: slack,                mode: read, default_in_scope: true}
        - {collection_name: m365_email_headers,   mode: read, default_in_scope: true}
        - {collection_name: m365_calendar,        mode: read, default_in_scope: true}
        - {collection_name: github,               mode: read, default_in_scope: true}
        - {collection_name: reflib,               mode: read, default_in_scope: false}

    # Per-agent memory — owner gets read_write to own memory
    - name: shape-memory-rw
      actor_kind: agent
      applies_to: [shape]
      entries:
        - {actor_id: shape, collection_name: shape-memory, mode: read_write, default_in_scope: true}
    # ... × 5 more agents
```

If #381 isn't fixed: replace the wildcard with explicit per-agent entries (6 entries × 7 collections = 42 entries in the agent-default profile + 6 per-agent-memory profiles).

## Phase 2 — Capture pre-flip baseline (~30 min)

```bash
ssh <your-vm-host> 'docker exec app-kairix-1 python3 /opt/kairix/.venv/bin/python -m scripts.cutover.capture_baseline \
    --flag topology_v2_collection_resolver \
    --out /var/lib/kairix/baseline-pre.json \
    --eval-suites reflib,production-queries \
    --latency-suite production-queries'
```

Capture: state digest (row counts + collection composition) + eval scores (recall, ndcg, hit_rate) + latency probe (p50/p95/p99) + sample-journey query results.

Save to `/data/development/baseline-pre-2026-06-DD.json` for the diff.

## Phase 3 — Flip the resolver flag (5 min)

Edit `/opt/kairix/app/kairix.config.yaml`:

```yaml
features:
  topology_v2_default_in_scope: true        # already true in Wave A
  topology_v2_collection_resolver: true     # ← the load-bearing flip
  # Per-connector v2 flags stay OFF (write path unchanged)
```

Restart:

```bash
ssh <your-vm-host> 'cd /opt/kairix/app && docker compose restart kairix kairix-worker'
```

Verify routing:

```bash
ssh <your-vm-host> 'docker exec app-kairix-1 kairix features status | grep topology_v2'
# Expect: topology_v2_collection_resolver  false true  ← effective ON
```

Quick smoke test:

```bash
docker exec app-kairix-1 kairix prep "what did we discuss about <project> last week" --agent shape
# Expect: hits from sharepoint + obsidian + slack + email + calendar (the in-default superset)
# NOT: hits from reflib (opt-in only)
# NOT: hits from builder-memory (cross-agent isolation)
```

## Phase 4 — 24-hour soak (1 day, mostly waiting)

The platform runs normally for 24 hours with the new resolver active. Watch for:

- Error log volume on kairix-worker (no spike of `ScopeProfileResolver` failures)
- Sync tick health (`kairix onboard check` stays all-green)
- Query latency (no p99 regression beyond ~20%)
- Dead-letter accumulation (no new entries)
- Operator-reported retrieval anomalies (your daily use, agent conversations)

If anything is alarming: flip `topology_v2_collection_resolver: false`, restart, investigate. The legacy path resumes; no data loss.

## Phase 5 — Post-flip baseline + diff (~30 min after soak)

```bash
# Same command as pre-flip, different out file
docker exec app-kairix-1 python3 -m scripts.cutover.capture_baseline \
    --flag topology_v2_collection_resolver \
    --out /var/lib/kairix/baseline-post.json \
    --eval-suites reflib,production-queries \
    --latency-suite production-queries

# Diff
python3 scripts/cutover/diff_baseline.py \
    --pre /var/lib/kairix/baseline-pre.json \
    --post /var/lib/kairix/baseline-post.json \
    --strict
```

**Hard thresholds** (from feature-flag-architecture.md):
- State delta: ±2% (document/chunk counts shouldn't shift)
- Eval scores: ±2pp (recall, ndcg, hit_rate)
- Latency: p95 within ±20%
- Sample-journey results: ≥80% parity

If any threshold breached: rollback + investigate. If all green: promote to the 1-week eval soak.

## Phase 6 — 1-week eval soak (your hard requirement)

Daily eval runs feed the deprecation decision:

```bash
# Add to cron / systemd timer (daily at 02:00 UTC)
docker exec app-kairix-1 kairix benchmark run --suite production-queries --output /var/lib/kairix/eval-history/
```

Daily score trend: if scores drift downward over the week, rollback. If stable: proceed to reap.

What to watch through the week:
- Eval scores trend
- Dead-letter accumulation by connector
- Sync tick error rate
- Operator-reported "I expected to find X but didn't" anomalies
- vec_index growth (should be flat — no new ingest behaviour)

## Phase 7 — Legacy reap (#374) — only after green 1-week soak

After the green light:

1. Remove legacy `collections:` block from kairix.config.yaml
2. Remove legacy `agents:` block (replaced by v2 scope_profiles + per-agent memory collections)
3. Retire the `topology_v2_collection_resolver` flag (delete from registry, default behavior becomes the new behavior)
4. Run the documents-table reap migration: delete rows with `collection IN ('home', 'projects', 'areas', 'resources', 'knowledge', 'archive', 'agent-knowledge')` AND `source_uri IS NULL` (the legacy path-based duplicates with no proper source URI)
5. Drop `DefaultCollectionResolver` class + the legacy-resolver tests

This is the final cleanup commit — ~half a day of work.

## Execution sequence (concrete order)

| Step | Action | Owner | ETA |
|---|---|---|---|
| 1 | Dispatch agent to fix #381 wildcard parse-order bug | subagent | 2h |
| 2 | Verify eval suite runs locally + on production | orchestrator | 30min |
| 3 | Author the full topology_v2 config locally | orchestrator | 1h |
| 4 | PR the config to <your-deploy-repo> (separate from the existing #465 PR) | orchestrator | 30min |
| 5 | Apply config to host VM via az run-command | orchestrator | 15min |
| 6 | Capture pre-flip baseline | orchestrator | 30min |
| 7 | Flip `topology_v2_collection_resolver: true` + restart | orchestrator | 15min |
| 8 | Smoke-test retrieval through MCP + CLI | orchestrator | 30min |
| 9 | 24h soak | wait | 1 day |
| 10 | Capture post-flip baseline + diff_baseline.py --strict | orchestrator | 30min |
| 11 | If green: enter 1-week eval soak | wait | 7 days |
| 12 | Daily eval check during the soak | orchestrator (1× / day) | 5 min/day |
| 13 | If green at week mark: open #374 reap PR | orchestrator | half a day |
| 14 | Merge #374, retire flag, drop legacy code | orchestrator | half a day |

**Total active work: ~1 day. Total elapsed: ~9 days (1d active + 1d soak + 7d eval soak).**

## What gets in the way

| Risk | Likelihood | Mitigation |
|---|---|---|
| Connector writes a different `collection=` value than v2 declares | Medium | Verify with `SELECT DISTINCT collection ...` before flipping; fix at connector code level or rename v2 collection |
| Wave A/B has a latent bug not caught by the test suite | Low | Soak surfaces issues; rollback path is one flag flip |
| Eval scores degrade beyond ±2pp threshold | Medium | The diff tool gates on this; rollback + investigate per-collection scoring |
| `applies_to: ["*"]` still fails after #381 fix | Low | Fallback to explicit per-agent entries; doesn't block cutover |
| Per-agent memory `path_filter` doesn't match what the legacy `write_path: 04-Agent-Knowledge/<agent>` writes | Medium | Check the chunk's source_uri shape; adjust path_filter glob |
| Operator unexpectedly hits a legacy-only collection name (e.g. queries `kairix prep --collection home`) | Low | If `home` is gone from v2 config, the resolver returns F21 error pointing at allowed collections; operator switches to a v2 collection name |

## Rollback procedure (~5 min, anytime)

```bash
# On the VM
sed -i 's/topology_v2_collection_resolver: true/topology_v2_collection_resolver: false/' \
    /opt/kairix/app/kairix.config.yaml
cd /opt/kairix/app && docker compose restart kairix kairix-worker
```

Legacy resolver routes again. No data loss (writes were never affected). Investigate the diff failure or operator-reported regression at leisure.

## What's NOT in this plan

- Per-connector flag flips (`topology_v2_obsidian: true` etc.) — separate future work; controls write path, not read path
- Migration of agents block to `paths:` multi-path schema (#115) — separate cleanup, not blocking
- Connector code refactors to read v2 cc_pair metadata at write time — separate future work
- vec_index per-batch reload throughput fix (#375) — separate stream
- Data lifecycle policy (#379) — separate ADR
- Dashboard work (`docs/architecture/dashboard-spec.md`) — separate stream

## Decision points before starting

1. **Fix #381 first, or use explicit per-agent listing?** — recommend fix first (2h investment, cleaner config)
2. **Which agent's slack tokens for the master?** — `shape` is the current pick; confirm before authoring the slack cc_pair
3. **GitHub repos for the allowlist?** — needs the 3 repo names from you (I have notes but they shouldn't go in the public config — will use placeholders in the PR + you fill the actual names in <your-deploy-repo>)
4. **UPN for m365_calendar / email?** — confirmed earlier as `<your-mailbox-upn>`
5. **Sample queries for the cutover baseline?** — need 5–10 representative queries; suggest reusing what's in the operator's daily agent prompts

Approve the plan + provide answers to the 5 decision points, and I'll start execution.
