# Guided configuration for connectors (KFEAT-022)

**Status:** design — pilot scoped for SharePoint; other connectors inherit the pattern after the pilot soaks.

## The gap

Today's connector configuration assumes the operator (or the agent doing the setup) already knows what to write into `kairix.config.yaml` — which SharePoint drive ids, which Slack workspace id, which GitHub installation id. The operator has to leave kairix, run raw API calls against the source platform, copy out the right opaque identifiers, paste them back into YAML, then validate. Even the existing `docs/getting-started/agent-driven-setup.md` recipe says "ask the user to run `kairix sharepoint list-sites`" — and that command doesn't exist.

The inversion is wrong: kairix has the credentials, so kairix should do the enumeration and present the choices in plain language. The operator (or agent) picks from a list; kairix writes the YAML.

This also matters because the source platforms differ in how aggressively their permissions default to "see everything." SharePoint is the worst case — `Sites.Read.All` lets the AAD app touch every drive in the tenant, and operators have no in-product signal about how much of that they're about to ingest until it's already happening.

## The shape

Three CLI subcommands per connector, each unlocking the next. SharePoint is the pilot; Slack / GitHub / Notion / future connectors inherit the same shape.

### `kairix <connector> discover`

Read-only enumeration using the credentials already provisioned. Surfaces:

- What the operator has access to (sites, channels, repos, page trees) in plain English.
- Per-entry volumetric: file count, total size, mime breakdown, age range. The operator sees "Marketing Hub — 23 drives, 4,128 files, 47 GB" before they pick it.
- Per-entry sensitivity hints from the source platform's own labels (Notion page sensitivity, SharePoint site classifications, Slack channel kind).
- Entries the credentials can NOT reach, surfaced as "would need: `Sites.Selected` grant on site X" so the operator knows what to ask their admin to widen.
- Estimated ingest time + disk impact (see §"Volumetric and progress" below).

Two output modes from the same code path:

- Human pager (default): coloured table, sensible defaults, "press q to quit" pager. Optimised for an operator at a terminal.
- `--json` machine output: same data, structured envelope. Optimised for an agent driving setup who needs to feed the choices back into another tool call.

Discovery never writes to KV, the config, or the index. The only side effect is the cache update (see §"Cache").

### `kairix <connector> configure --pick <selection>`

Takes the discovery output and the operator's selection, asks any remaining choices (which sensitivity tier, which credential bundle to bind to), and emits a `topology.connectors:` YAML block. Two modes:

- Default emit-to-stdout: operator inspects, pastes into their config manually. Safe — no shared state touched.
- `--write-to <path>` (default `kairix.config.yaml`) merges in place with a backup. See §"In-place merge with backup" below.

After write (in `--write-to` mode), runs `kairix config validate`. If validation fails, rolls back from the backup automatically and surfaces the validation failure.

### `kairix <connector> status`

Once a cc_pair is materialised and syncing, this surface shows per-cc_pair progress, throughput, ETA, disk impact, and orphan-vector counts. Same dual human / `--json` mode. See §"Volumetric and progress" for the data model.

## Cache

Discovery hits remote APIs. Re-running discovery in a back-to-back pipeline (discover → preview → configure → re-discover-to-verify) shouldn't pay the API cost three times.

**Cache location.** `~/.cache/kairix/discovery/<connector>-<credential-bundle-id>.json` for pip; `/data/kairix/discovery/...` for Docker. Per-credential-bundle keying avoids cross-talk when the operator rotates credentials — a new credential id gets its own cache file, not a stale shared one.

**Cache shape.** JSON envelope with `cached_at` (ISO timestamp), `credential_bundle_id`, `connector_version`, `entries: [...]`. Connector version is included so a connector upgrade that changes the discovery output shape invalidates older caches automatically.

**Default TTL: 24 hours.** Discovery output is operator-curated (sites, repos, channels) which doesn't change minute to minute. Anyone who wants fresher needs:

- `--refresh` — force a fresh API call regardless of cache age.
- `--cache-max-age=Nm/Nh/Nd` — per-invocation override (e.g. `--cache-max-age=1h` for a recent admin change).
- `kairix discovery clear [--connector <name>]` — drop the cache file(s).

**Cache age in output.** When discovery returns from cache, the header line names the cache age explicitly: `(cached 3h12m ago — pass --refresh to re-fetch)`. The operator never wonders whether they're looking at stale data.

## In-place merge with backup

`--write-to <path>` is convenient but writes shared state, so it has two safety properties:

**Backup before write.** Before touching `<path>`, copy it to `<path>.backup-YYYYMMDDTHHMMSS`. The backup naming uses a sortable timestamp so the most recent is the lexicographic last in `ls`. Retain the last N backups (default 5; older ones auto-prune) so a series of mis-merges can still be unwound.

**Merge strategy.** Locate the `topology.connectors:` block (creating it if absent). For each new connector entry in the emit:

- **Same `id`:** replace the existing entry in place (idempotent re-run).
- **New `id`:** append.

YAML round-trip via `ruamel.yaml` (preserves comments + key order) — `pyyaml`'s safe_load + safe_dump would strip the operator's inline comments and is unsuitable.

**Validate after write.** Run `kairix config validate` against the merged result. If validation fails:

1. Copy the backup back over the merged file (rollback).
2. Surface the validation failure with the `fix:` / `next:` / `run:` shape — and name the backup path so the operator can inspect what was attempted.

Exit non-zero on rollback so a calling agent or script sees the failure.

**Manual recovery.** `kairix config rollback --backup <path>` swaps the named backup back into place (with another backup of the current state for safety). Operators get a clear recovery command, not "go find the backup file."

## Volumetric and progress

Operators currently have no signal about what indexing 1 TB of SharePoint documents will cost. The guided-configuration surfaces own this signal end to end.

### Pre-ingest estimate (in `discover` output)

Per source unit (drive / repo / channel / page tree), surface:

- **File count + total raw size** from the source platform's metadata (Graph's `size` field, GitHub's blob sizes, Notion's page count, Slack's channel-history estimate).
- **MIME breakdown** — operators need to see that 80% of a drive is `.pptx` (which extracts to text well but is slow) vs 80% `.zip` (which kairix can't extract and will skip).
- **Estimated post-extraction text volume.** PowerPoint extracts to ~5% of binary size; PDF to ~10%; markdown to 100%. Apply per-mime ratios.
- **Estimated index growth.** Per the dogfood ratio: each MB of extracted text → ~80 KB FTS5 index + ~30 KB usearch vectors (Azure embed) + Neo4j entity nodes proportional to entity density.
- **Estimated ingest time.** Per-mime throughput (embed-bound for text, extract-bound for binary). Pulls from a calibration file the worker maintains (`~/.cache/kairix/throughput-calibration.json`), seeded with defaults but updated by every real ingest run so estimates converge to the operator's actual hardware.

Render as a single summary line per source unit: `Marketing Hub — 4,128 files, 47 GB raw → ~3.8 GB text, ~480 MB index, est. 6h 20m on this host`.

### During-ingest progress

Two surfaces consume the same underlying state:

- **CLI:** `kairix <connector> status` shows per-cc_pair `files_fetched / files_total`, `bytes_processed / bytes_total`, `current_phase` (sync / extract / chunk / embed / persist), `throughput_files_per_min`, `eta`, `last_error` (if any).
- **MCP:** `tool_ingest_progress` returns the same data as a structured envelope. Agents querying "is kairix ready for me to use yet?" get a typed answer instead of guessing.

State is persisted to the same `WorkerState` JSON the maintenance loop already uses (KFEAT-021) — extends the shape, doesn't introduce a new persistence surface.

Operators (and agents) can also subscribe to progress events via structured logs:

```
event=ingest_progress cc_pair_id=4 source=sharepoint phase=extract
  files_done=420 files_total=4128 bytes_done=4.8GB bytes_total=47GB
  throughput_files_per_min=11.2 eta_iso=2026-05-25T15:42:00+10:00
```

Same shape as the existing `event=maintenance_tick_*` logs. Log shippers + dashboards consume both with one filter pattern.

### Post-ingest "fully operationalised" signal

Today the operator has no clear marker for "indexing complete, kairix is operational with the new collections." The `discover` → `configure` flow ends in:

```
✓ Synced 12,840 files from 3 SharePoint connectors.
✓ Extracted, chunked, embedded — index is current.
✓ Neo4j entity graph rebuilt — 1,847 entities, 12,103 relationships.
✓ Reflib benchmark: NDCG@10 = 0.876 (baseline pre-add: 0.881 — within ±0.02 tolerance).

Your new SharePoint collections are ready. Try:
  kairix search "Q3 OKRs"             — search across everything
  kairix search "Q3 OKRs" --collection sharepoint-marketing
  kairix brief "what's blocking the launch"
```

The benchmark step is the explicit "fully operationalised" gate — it confirms the index is queryable end-to-end against a real eval pass, not just that bytes landed on disk. Operators get a concrete pass/fail rather than guessing at completion.

## Safe defaults

Throughout discovery + configure + status, safe defaults frame the operator toward the conservative pick:

- **Discovery surfaces everything, including high-volume drives.** It doesn't pre-filter — operators need to see the scale before deciding.
- **Configure defaults to emit-to-stdout, not in-place merge.** Writing shared infra is opt-in.
- **Configure requires explicit `--accept-volumetric` past a threshold.** If the user-picked set exceeds 100k files OR 50 GB raw OR 1h estimated ingest, configure refuses without `--accept-volumetric` and surfaces the totals. Stops accidental "select all" on an enterprise tenant.
- **Sensitivity hints surface in the discovery table.** Sites the source platform flagged as confidential (SharePoint sensitivity labels, Notion page-level restrictions) display the hint in the row — the operator sees the warning before they pick, not after.
- **Per-actor scope grants are NOT inferred from connector additions.** Adding a connector adds a cc_pair; granting agent X access to its collection is a separate explicit step. Stops "I added a connector and agent-alpha can now see the legal drive I forgot to scope."

## SharePoint pilot — implementation deep dive

The pilot lands the four pieces above end to end for SharePoint, behind a single feature flag `guided_configuration_sharepoint` (default off). Each piece is a separable commit; the cutover protocol promotes the flag once all four have soaked together.

### Module layout

```
kairix/connectors/sharepoint/
├── discover.py           # NEW — Graph enumeration → DiscoveryResult
├── configure.py          # NEW — DiscoveryResult + selection → YAML emit
├── volumetric.py         # NEW — per-drive size/count/mime + throughput calibration
└── ... (existing files unchanged)

kairix/cli.py             # Extended: sharepoint discover / configure / status subcommands
kairix/agents/mcp/server.py   # Extended: tool_sharepoint_discover / tool_ingest_progress
kairix/platform/discovery/    # NEW shared module — cache, backup, merge utilities
├── cache.py
├── backup.py
└── yaml_merge.py
```

The connector code itself stays unchanged — the new modules are siblings that consume the existing `SharePointGraphClient`. F35 / F38 stay clean.

### Per-piece scope

**Piece 1 — `kairix sharepoint discover`** (one commit):
- `discover.py:enumerate_sites_and_drives(graph)` returns a `DiscoveryResult` frozen dataclass.
- `cache.py:read_or_fetch(key, fetcher, max_age)` reads + writes the discovery cache; returns `(result, cache_age_seconds | None)`.
- `kairix sharepoint discover [--refresh|--cache-max-age=...|--json]` subcommand wires the two.
- BDD: 2 scenarios (fresh discovery; cached discovery surfaces the age). Contract test against a scripted `SharePointGraphClient`. Integration test exercising cache hit + cache miss. F30 outcome test asserts the human + JSON output shapes.
- No volumetric estimate yet — that's piece 3. The output names file counts only.

**Piece 2 — `kairix sharepoint configure --pick`** (one commit):
- `configure.py:emit_connector_block(discovery_result, picks, sensitivity, credentials_ref)` returns the YAML block as a string.
- `yaml_merge.py:merge_topology_connectors(config_path, new_block)` does the in-place merge via `ruamel.yaml`.
- `backup.py:backup_then_write(path, new_content, max_backups=5)` writes the backup, runs the merge, validates, rolls back on failure.
- `kairix sharepoint configure --pick 1,2,3 [--write-to <path>|--sensitivity X|--credentials Y|--json]` subcommand wires them.
- BDD: 4 scenarios (emit-to-stdout; in-place merge happy path; in-place merge with validation failure → rollback; idempotent re-run replaces same-id entry). Integration test exercising the `kairix config validate` round-trip. F30 outcome test asserts the backup file lands at the right name + the rollback path works.

**Piece 3 — volumetric estimate** (one commit):
- `volumetric.py:estimate_drive(graph, drive_id)` returns `DriveVolumetrics` (file_count, total_bytes, mime_breakdown, estimated_text_bytes, estimated_index_bytes, estimated_ingest_seconds).
- `throughput-calibration.json` schema + read/write helpers.
- `discover` output extended to include the volumetric line per drive.
- `configure` extended to refuse past the `--accept-volumetric` threshold.
- BDD: 3 scenarios (small drive — no warning; large drive over threshold — refuses; large drive with `--accept-volumetric` — accepts). Integration test exercising the throughput calibration read/write.

**Piece 4 — `kairix sharepoint status` + `tool_ingest_progress`** (one commit):
- Extend the existing `WorkerState` JSON to carry per-cc_pair ingest progress.
- Worker writes progress on each batch boundary; `kairix sharepoint status` reads + renders.
- New MCP tool `tool_ingest_progress` returns the same data as a structured envelope.
- Structured logs (`event=ingest_progress`) emit alongside.
- BDD: 2 scenarios (in-progress ingest renders progress; completed ingest renders the "fully operationalised" summary). F30 outcome tests cover the CLI + MCP surfaces.

### F-rule discipline

Each piece carries the canonical test set: BDD ON + OFF for the flag (F54), contract test against the canonical fake (F43), integration test exercising both flag branches (F47), E2E composed-path test (F48). The flag `guided_configuration_sharepoint` lands in the registry with the cutover plan and `target_retire_in: v2027.5.24` (12 months — matches the topology_* fleet so retirement batches together when the discovery surface generalises to all connectors).

### Cutover protocol

Per `docs/architecture/feature-flag-architecture.md` §4.2 — the four pieces soak together behind the flag:

1. Land pieces 1-4 on main, each behind `guided_configuration_sharepoint` (default off).
2. Internal dogfood: operator flips the flag on the dogfood host, runs `discover` against the existing SharePoint creds, picks a small site, runs `configure --write-to`, watches `status` until completion, confirms search works.
3. After 48h of clean dogfood: capture baseline (state digest + reflib eval scores + sample-journey results from `kairix search` against the new collection), flip stage from `introduce` to `cutover`, default still false.
4. After 4 weeks of cutover-stage soak: flip default to true, the new surfaces become the documented path, the legacy "paste drive IDs by hand" recipe gets removed from `agent-driven-setup.md`.
5. After flag retirement: the four subcommands are unconditional; the flag check is deleted.

## Generalisation to other connectors (post-pilot)

Once the SharePoint pilot lands, the pattern generalises with mechanical effort. Each connector inherits:

- A `kairix/connectors/<name>/{discover,configure,volumetric}.py` module trio.
- Three CLI subcommands (`<name> discover`, `<name> configure`, `<name> status`).
- Three MCP tools (`tool_<name>_discover`, `tool_<name>_configure`, `tool_ingest_progress` — the last is connector-agnostic).
- A `guided_configuration_<name>` flag with the same cutover plan.

Connector-specific differences from SharePoint:

- **Slack:** `discover` enumerates workspaces + channel counts; volumetric is message count × average message length (Slack channels are small per-item but high count). No drive-equivalent — the natural scope unit is the channel.
- **GitHub:** `discover` enumerates installations + repo counts per installation; volumetric is `git ls-tree | wc -l` + total blob size. App-installation path already provides natural scope ("Only select repositories"); discovery surfaces what's selected.
- **Notion:** `discover` enumerates pages + databases shared with the integration; volumetric is page count + nested block count. Natural scope from the source platform — discovery just reports.

The shared `kairix/platform/discovery/` module covers cache + backup + YAML merge for all of them — those are connector-agnostic.

## Open questions

- **Throughput calibration cold-start.** The first ingest on a new host has no historical data — the estimate is purely from the seeded defaults. Should the estimate carry an "uncalibrated" tag the first time, and trust the operator's tolerance until calibration has 10+ samples?
- **Discovery cache invalidation on permission change.** If the operator removes the AAD app's access to a site between discovery runs, the cache could show entries that no longer resolve. Defaulting to 24h TTL is probably fine; worth re-checking after the pilot soaks.
- **Configure write-to behaviour when `topology_config` flag is off.** The configure step writes config that's inert until `topology_config` is also on. Should configure refuse with `--accept-inert`, warn-and-proceed, or stay silent? Lean warn-and-proceed.
- **MCP tool authorisation.** `tool_sharepoint_configure` writes shared infra. The MCP layer has no built-in auth today; should this tool be gated by a separate `mcp_allow_config_writes` flag the operator opts into?

These resolve during the pilot dogfood — none are blocking for landing.
