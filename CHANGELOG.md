# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Calendar Versioning (CalVer)](https://calver.org/) — `YYYY.MM.DD`, with `.N` suffix for same-day releases.
Git tags: `v2026.04.18`. Deploy by pinning to a tag: `pip install git+...@v2026.04.18`.

## [Unreleased]

## [2026.5.27a2] - 2026-05-27 — Disk-space cascade fix + escalation framework class (alpha)

> **Upgrading?** Urgent fix for the disk-space cascade observed on the v2026.5.27a1 dogfood — 8,090 of 8,396 SharePoint reextract attempts failed with `[Errno 28] No space left on device` because one pathological PPTX expanded to fill the 2GB tmpfs and every subsequent extraction leaked an empty tmpfile stub (8,087 placeholders accumulated). Two fixes ship together: the leak is sealed, AND extractor scratch moves off tmpfs onto the bind-mounted runtime disk so a single bad file can't exhaust capacity. Full notes: [`docs/upgrades/v2026.5.27a2.md`](docs/upgrades/v2026.5.27a2.md).

### Fixed

- **Markitdown extractor no longer leaks empty tmpfile stubs on write failure.** Pre-fix the `tmp.write(raw)` step lived inside `with tempfile.NamedTemporaryFile(delete=False)` but outside the try/finally that called `tmp_path.unlink()`. A write failure (e.g. ENOSPC because tmpfs was already near-full) left the empty placeholder on disk; over a single sync cycle 8,087 of these accumulated, filling tmpfs further. The fix moves the write step inside the try block so the finally clause unlinks the placeholder on write failure too. Structural assertion test locks the line order so a future refactor that re-inverts the shape trips a clear regression message.

- **Onboard healthcheck no longer reports unhealthy on the v2026.5.27a1 image** — the new `check_extractor_libraries_importable` step (from #322) correctly caught that the `ocr` extractor's `pytesseract` library wasn't installed in the runtime image. The Dockerfile now installs the `[ocr]` extra (`pytesseract`, `opencv-python-headless`, `Pillow`, `pdfplumber`) and the tesseract-ocr system binary (the C++ engine pytesseract wraps).

### Changed

- **Extractor scratch moved off tmpfs onto the bind-mounted runtime disk** — `TMPDIR=/data/kairix/tmp` ships in the image. Python's `tempfile` honours this so every extractor's tempfile lands on the disk-backed volume (typically tens of GB available) instead of the 2GB tmpfs at `/tmp`. A pathological extraction can no longer exhaust scratch capacity on a single file. The 2GB `/tmp` tmpfs from #317 stays in compose for general OS scratch, but the high-volume kairix path no longer fights for that 2GB.

### Added

- **`EscalatingExtractor` framework class** (`kairix.core.connectors.escalation.EscalatingExtractor`) — ordered chain orchestration over the Extractor Protocol. Wraps a sequence of extractors, walks the chain via `quality_ok`, returns the first member whose output is good enough. Exhaustion (every member returns False) surfaces the longest-markdown attempt with a `trace.exhausted=True` marker so operators still get something indexable + the observability to see degradation. Exceptions in one tier don't kill the chain — logged and the next tier runs. This is part 1 of #23; the wiring into `_run_one_connector_batch` and `_build_reextract_components` (via `extractor_chain: [a,b,c]` opt-in config field) ships in a later release alongside BDD + F30 outcome tests.

### Things that haven't changed

- Single-extractor `extractor: <name>` config — fully unchanged. The escalation chain is opt-in via `extractor_chain: [...]` in a later release.
- Connector Protocol surface, on-disk bronze layout, chunk-writer contract.
- The 2GB tmpfs at `/tmp` (compose `tmpfs:` block from #317) — still there for general OS use; just no longer the default for kairix's own extractor scratch.

## [2026.5.27a1] - 2026-05-27 — Markitdown converters, oversized-chunk split, dead-letter recovery (alpha)

> **Upgrading?** Three fixes from the v2026.5.26a1 SharePoint dogfood. No config edits required. If you have dead-letter rows from a pre-upgrade run, the new `kairix worker reextract` recovers them in-place — see the upgrade notes. Full notes: [`docs/upgrades/v2026.5.27a1.md`](docs/upgrades/v2026.5.27a1.md).

### Fixed

- **Markitdown's DOCX, XLSX, PPTX, and Outlook MSG converters are now installed in the production image (#322).** v2026.5.26a1 shipped with `markitdown[pdf]` only; every Office document failed extraction with `MissingDependencyException`. The SharePoint dogfood produced 8,785 dead-lettered items on first run because of this. The Dockerfile now installs `markitdown[pdf,docx,xlsx,pptx,outlook]` so every documented format works out of the box.

- **Silver chunker no longer leaves oversized paragraphs as one giant chunk (Bug B).** A single 2,000-character paragraph used to land as one chunk that overshot the target. The chunker now splits at sentence boundaries first, then word boundaries if a sentence is still oversized, then character boundaries as a last resort — every chunk stays under the target without breaking semantic boundaries unnecessarily. Three new helpers (`_split_long_paragraph`, `_split_long_sentence`, `_split_long_word`) plus a pre-expansion step in `_chunk_markdown`.

### Added

- **`kairix worker reextract --source-name <name>` for dead-letter recovery (Bug D).** Walks `DeadLetterStore.list(source_name)`, looks up each item's `bronze_records` row, re-runs `extractor.extract(raw, mime)` through the currently-registered extractor, runs silver → chunk_writer → entity-graph sink, then clears the dead-letter row. Commits per item so partial recovery is durable. `--dry-run` walks the same logic without committing (useful for sizing the recovery first). `--limit N` caps the number of items processed. Recovers the 122 SharePoint items still in dead-letter after #322 shipped without forcing operators to delete + re-ingest the whole source.

- **`DeadLetterStore.clear(source_name, item_id)`** — caller-owned transaction (matches the rest of the store), idempotent (returns False when no row existed). Used by the reextract path so a clear() that lands alongside a successful chunk write commits atomically.

- **`check_extractor_libraries_importable` onboard check.** Imports every library each registered extractor declares as a dependency, at startup. Missing libraries fail the check with the install command in the remediation. Catches the same regression class as #322 before items hit the dead-letter table.

### Things that haven't changed

- Connector Protocol surface, on-disk bronze layout, chunk-writer contract — all three fixes are purely additive at the API surface.
- Existing chunks written by v2026.5.26a1 silver remain valid; the chunker change adds splits for oversized paragraphs but doesn't re-shape chunks that were already within target.
- `DeadLetterStore.record` / `is_poisoned` / `list` — `clear()` is additive.

## [2026.5.26a1] - 2026-05-26 — Per-chunk commit + cold-start fix + OSS evaluation outcome (alpha)

> **Upgrading?** Straight pull from a25a1 — purely internal changes. No config edits required. Full notes: [`docs/upgrades/v2026.5.26a1.md`](docs/upgrades/v2026.5.26a1.md). Architectural decisions recorded in [`docs/architecture/ADR-018-dlt-connector-framework.md`](docs/architecture/ADR-018-dlt-connector-framework.md) (Wave 1 chunking outcome) and [`docs/architecture/connector-oss-library-evaluation.md`](docs/architecture/connector-oss-library-evaluation.md) (post-ADR-018 reset; 4 OSS frameworks evaluated, all rejected on raw-blob fit).

### Fixed

- **`ConnectorPipeline` no longer orphans a whole batch's bronze writes on a single mid-batch failure (#321).** Previously the pipeline ran the entire batch as one SQLite transaction; a Silver / writer / sink failure rolled back every uncommitted `bronze_records` row but left the on-disk blobs already fsynced. SharePoint backfills of ~6000 items leaked thousands of orphans on every worker restart. The pipeline now commits every `chunk_size` items (default 50); a failure rolls back only the current chunk; previous chunks stay committed and the cursor advances per chunk.

- **MCP cold-start no longer hands agents an opaque `fetch failed` (#320).** The MCP server used to run the warm sequence (7–30 s) synchronously BEFORE binding the HTTP port. During that window agents got connection-refused at the OS network layer, which JavaScript `fetch()` reports as the opaque string `"fetch failed"`. The fix moves warm to a background daemon thread so uvicorn binds the port immediately; the existing `ColdStartMiddleware` then returns a structured 503 + `ColdStart` envelope for every call during warm.

- **Cold-start envelope rewritten for positive-action affordance (#320).** The previous `agent_instruction` was three stacked prohibitions (`"Do not answer from memory, do not use a lower-quality fallback, do not treat this as a completed retrieval"`). The new shape leads with positive `next:` and `fix:` actions per `feedback_agent_prompts_positive_assertion`: `"next: pause retry_after_ms then call this same tool again. fix: if the second call still returns ColdStart, surface 'kairix still warming after ~8s' to the user and ask whether to proceed without retrieval — this is a transient process-boot state, not a hard failure."` Anti-pattern guards added in tests so the prohibition stacking can't regress.

### Changed

- **`dex_crm` connector retry now uses `tenacity` instead of a hand-rolled loop.** Replaces ~30 LoC of `while True` retry with `tenacity.Retrying(retry_if_result(...), wait_exponential, stop_after_attempt)`. Same observable behaviour (the 12 existing dex_crm tests pass unchanged including the 429-retry-with-backoff scenario); the change is the reference shape future connectors should copy when they need HTTP retry with backoff. Rationale recorded in `connector-oss-library-evaluation.md` §7.

- **Operator guidance for the runtime disk choice (`docs/operations/OPERATIONS.md` §5.6).** The previous v2026.5.25 note said "data on the data disk." That assumed a deploy shape where the data disk is the largest one. The revised guidance is "kairix runtime on whichever disk is largest" with a `df -h` check — the dogfood VM has a 256 GB OS disk and a 64 GB data disk, so kairix runtime lives on `/var/lib/kairix-runtime` not `/data`.

### Added

- **`tenacity>=8,<10` as a base dependency.** One new dep, no transitives. Used today by the `dex_crm` connector retry; available to any future connector that needs HTTP retry with exponential backoff.

- **OSS connector library evaluation (`docs/architecture/connector-oss-library-evaluation.md`).** Four candidates (dlt, Airbyte Python CDK, Meltano Singer SDK, PyAirbyte) evaluated in parallel against hard requirements derived from kairix's architecture. All four rejected on H1 (raw binary blobs as first-class output). Recommendation: stay homegrown + adopt tenacity. The methodology is the reference shape for future "should we adopt OSS library X" decisions — requirements first, parallel research, evidence converges. The previous ADR-018 cost ~2 days of failed pivots to reach the same conclusion; this evaluation took ~3 hours.

### Things that haven't changed

- Default behaviour for connectors whose batches stay under 50 items (Obsidian, typical Slack/GitHub deltas) — the chunking is invisible at the API surface.
- The bronze write contract itself. Per-chunk commits are an internal transaction-granularity change; `BronzeStore` Protocol is unchanged.
- Every per-connector cursor, dead-letter, and Protocol shape.
- The MCP cold-start envelope's machine-readable fields (`status`, `error_code`, `retry_after_ms`, `estimated_seconds_remaining`, `tool`). Only the human-facing `guidance` and `agent_instruction` strings changed shape.

## [2026.5.25a1] - 2026-05-25 — Disk pressure, bronze hygiene, version-drift fixes (alpha)

> **Upgrading?** Five fixes from the 2026-05-24 SharePoint dogfood. No config change required; flags ship OFF. If you set `KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD=999999` as a band-aid, drop it after upgrading. Full notes: [`docs/upgrades/v2026.5.25a1.md`](docs/upgrades/v2026.5.25a1.md).

### Fixed

- **`/tmp` no longer fills the host root filesystem (#317).** Both `kairix` and `kairix-worker` ship a 2 GB tmpfs `/tmp` mount in the canonical compose files. SharePoint binary downloads and markitdown PDF/PPTX conversions stay bounded by RAM instead of spilling onto the OS disk. Operators with custom override files inherit the default unless they explicitly remove it.
- **Bronze raw blobs no longer accumulate forever as orphans (#318).** The maintenance scheduler ships a new stage that walks every connector's bronze tree on each tick and deletes any file with no `bronze_records` row. A 5-minute grace period skips files mid-fsync. The 2026-05-24 dogfood reaped 36 GB of orphan SharePoint blobs accumulated over a single backfill.
- **`connector_sync` no longer freezes when the local vault is idle (#312).** Local-only maintenance tasks (`entity_seed`, `health_check`, `wikilinks_inject`) still skip after the configured embed-noop streak, but external-source discovery now runs on its own interval regardless. A quiet local vault doesn't imply quiet upstream sources.
- **Worker no longer silently downgrades to `:latest` after a manual `docker compose up` (#313).** The alpha-deploy webhook now writes `KAIRIX_IMAGE_TAG=<version>` to `/opt/kairix/app/.env`. Subsequent ad-hoc `docker compose up -d --force-recreate` commands interpolate the same tag instead of picking up whatever was last pulled.

### Added

- **`bronze_ttl_gc` feature flag (default OFF) for long-term bronze growth bounds (#316).** When ON, the maintenance scheduler deletes bronze raw blobs and their `bronze_records` rows older than `KAIRIX_BRONZE_TTL_DAYS` (default 7). Default OFF until each deploy validates that re-fetch from source is reliable for every configured connector.
- **Operator guidance for data-on-data-disk layout** in [`docs/operations/OPERATIONS.md` §5.6](docs/operations/OPERATIONS.md). The default Docker named volumes live under `/var/lib/docker/volumes/` on the OS disk; this section walks through the bind-mount layout that survives a real backfill.
- **Meta-issue #319 for test discipline gaps**: F45 / F48 force scenarios for new capabilities but not for failure modes or lifecycle of accumulating state. Three production defects in this release (#312, #316, #318) had zero BDD/E2E coverage before they shipped because the discipline didn't require it. Proposed F62 / F63 / F64 to close the gap; will land as separate baseline-building work.

### Things that haven't changed

- Default behaviour for any operator who isn't using connectors. The orphan reaper and TTL GC stages run inside the `maintenance_loop` flag-gated tick — both are no-ops when that flag is OFF.
- The bronze write contract itself. Atomicity was already correct (filesystem first, then SQL; caller commits) — the gap was the missing sweeper for the crash-window orphans the docstring already anticipated.
- Every connector's Protocol surface, cursor format, or chunk-write contract.

## [2026.5.24a4] - 2026-05-25 — SharePoint per-drive path filtering (alpha)

> **Upgrading?** Straight pull from a3 — the new behaviour is purely additive. Set `include_paths` / `exclude_paths` on a SharePoint drive entry only when you want to scope a drive by folder. Full notes: [`docs/upgrades/v2026.5.24a4.md`](docs/upgrades/v2026.5.24a4.md).

### New for operators

- **Per-drive folder filtering for the SharePoint connector.** Two new optional lists on each drive entry — `include_paths` and `exclude_paths`. Empty = whole drive walked (current behaviour). Non-empty `include_paths` = only items whose path starts with one of the listed paths land. `exclude_paths` drops matching items even when an include matched. Segment-boundary prefix match, case-insensitive. Unblocks pointing at SharePoint drives that mix curated content and bulk material (Microsoft partner libraries, archived projects, draft folders) without splitting content across drives in SharePoint itself.
- **Startup probe for missing include paths.** When `include_paths` is set, the connector calls Graph once per path at startup to confirm the folder exists. Missing folders surface as `sharepoint_probe_missing_folder` log warnings — the connector continues syncing the present paths so a typo doesn't silently drop your data.
- **Parse-time refusal of exact include/exclude overlap.** `include_paths: ["/Foo"]` + `exclude_paths: ["/Foo"]` (almost always a copy-paste typo) is refused at `kairix config validate` with a fix pointer. Strict children (e.g. include `/Foo` + exclude `/Foo/draft`) remain legal — that's the intended use case.
- **Auto-synthesised cc_pair labels** for filter-active drives without an operator-set `display_name`. Two cc_pairs against the same drive with different `include_paths` now surface as distinguishable labels in `kairix features status`, `tool_features_status`, and structured logs — instead of two identical opaque drive ids.

### Behaviour changes worth noting

- **Backward-compatible by default.** Existing SharePoint deployments that don't set `include_paths` / `exclude_paths` see no change. The filter is a per-tick view, not persisted state — operators can change `include_paths` between syncs without invalidating the per-drive deltaLink.
- **First sync still walks the full delta stream.** The filter is client-side post-processing — Graph's delta endpoint doesn't take a path filter. Only the extract + embed + index work is reduced for filtered items, not the upstream listing cost. Per-folder delta optimisation is a noted future enhancement.

### Internal

- **KFEAT-022 prerequisite.** Per-folder scoping is the first piece of the guided-configuration epic — without it, `kairix sharepoint discover` would surface drives the operator can choose but not their internal folder structure. Path filtering unblocks the discovery surface walking one level deeper. See [`docs/architecture/guided-configuration.md`](docs/architecture/guided-configuration.md).
- **23 new tests across unit / contract / integration / E2E / BDD layers** following the spec in [`docs/architecture/sharepoint-path-filtering.md`](docs/architecture/sharepoint-path-filtering.md). All green; pre-existing SharePoint test suites continue to pass (purely additive change).

### Things that haven't changed

- The Graph API permissions you grant — `Sites.Read.All` + `Files.Read.All` still cover this.
- Every other connector. Slack, GitHub, Notion, M365, Obsidian, Dex unchanged. Path-filter generalisation to those connectors is part of KFEAT-022.
- The cursor format, the connector's Protocol surface, the chunk-write contract.

## [2026.5.24a3] - 2026-05-24 — Configurable agent-knowledge layout for onboard check (alpha)

> **Upgrading?** Straight pull from a2 — no config change required. The default glob is broader than earlier alphas, so vaults that previously failed `agent_knowledge_populated` will now pass it. Full notes: [`docs/upgrades/v2026.5.24a3.md`](docs/upgrades/v2026.5.24a3.md).

### New for operators

- **`agent_knowledge_populated` onboard check now reads its layout from `kairix.config.yaml`.** Two new optional keys live under `paths:` — `paths.agent_knowledge_dir` (default `04-Agent-Knowledge`) names the directory; `paths.agent_memory_glob` (default `**/*.md`) is the recursive glob that identifies memory files. The new default accepts any `.md` file anywhere under the agent-knowledge tree, so vaults using `<agent>/<date>.md`, `<agent>/memory/<date>.md`, or a shared `memory/` directory all pass without operator action. Pin a stricter glob (e.g. `*/memory/*.md`) if you want the check to enforce a specific per-agent layout.
- **The check's failure detail names the glob it searched.** When the check fails, the message includes both the directory it looked in and the pattern it applied, so the layout-vs-config mismatch is visible at a glance.

### Behaviour changes worth noting

- **Onboard healthcheck reliability.** This fixes a sharp edge in v2026.5.24a2 deployments where the docker compose healthcheck would mark the kairix container unhealthy if the operator's vault didn't match the previously-hard-coded `<agent>/memory/<file>.md` layout. The broader default glob accepts more layouts; the config knobs let strict layouts opt back in.

### Things that haven't changed

- Every other onboard check. Only `agent_knowledge_populated` is affected.
- The briefing pipeline. It continues to read from `04-Agent-Knowledge/<agent>/memory/`; a follow-up will reconcile the briefing resolver with the same config knobs so both surfaces read from one place.
- Every feature flag from v2026.5.24a2 — pulling this version does not flip any flag.

## [2026.5.24a2] - 2026-05-24 — Slack + GitHub + Notion connectors + maintenance loop (alpha)

> **Upgrading?** Nothing required — every new connector and the maintenance loop are gated by default-off feature flags. To opt into any of them, follow [`docs/upgrades/v2026.5.24a2.md`](docs/upgrades/v2026.5.24a2.md).

### New for operators

- **Slack connector** behind `connector_slack`. Pulls public, private, and direct-message channels via the Slack Web API, plus a Socket Mode listener for live events. Sensitivity is set per channel kind (public → internal, private → confidential, DM → personal) so the F39 routing applies without operator config. Per-cc_pair token rotation + reconnect-with-jitter on disconnect.
- **GitHub connector** behind `connector_github`. Pulls code (commits + blobs), issues, and pull requests via REST + GraphQL, with a webhook listener for push / issues / pull_request / installation_repositories events. Personal access tokens or app installations both work — the connector picks whichever credential resolved. Abuse-detection backoff is automatic when GitHub returns secondary rate-limit responses.
- **Notion connector** behind `connector_notion`. Pulls workspace pages plus database rows via the public API, renders block trees to Markdown, then dispatches the output to the existing kairix extractor registry (passthrough or markitdown).
- **Per-source topology v2 pilots for the existing connector fleet.** Seven new flags — `topology_v2_dex_crm`, `topology_v2_m365_email_headers`, `topology_v2_m365_calendar`, `topology_v2_sharepoint`, `topology_v2_slack`, `topology_v2_github`, `topology_v2_notion` — each emit one Container per source-side unit (tenant, mailbox, calendar, drive, channel, repository, page tree) with its own delta cursor. Default-off; mirrors the obsidian pilot shape landed in v2026.5.24a1.
- **Background maintenance loop** behind `maintenance_loop`. When the flag is on, the worker fires a periodic tick (default every 24 hours, tunable via `KAIRIX_MAINTENANCE_INTERVAL_S`) that prunes orphaned `content_vectors` rows into a soft-delete table with a 7-day retention window before hard-deleting. Replaces the reactive preflight orphan check — ghost search hits get cleaned up in the background instead of blocking the next ingest.
- **New surfaces.** `kairix worker maintenance` fires a one-shot maintenance tick on demand. `kairix worker preflight --auto-heal` now prunes orphan vectors alongside its existing FTS auto-heal. `kairix features status --maintenance` shows loop cadence + last-tick timestamp + cumulative orphan count.
- **One new onboard check.** `maintenance_loop_ticking` reports whether the worker has fired a tick within the expected interval when the flag is on, and skips with `ok=true` when the flag is off. Default deploy now reports 15 checks (was 14).

### New for agents

- **Connector recipes in `docs/getting-started/agent-driven-setup.md`** for each new connector — what secret to ask the user for, what env-var name to set it under, what `topology_v2.connectors:` entry to write, what flag to flip. Every recipe follows the positive-pattern affordance shape (fix + next + run) instead of a "don't do X" list.

### Behaviour changes worth noting

- **Default deploy reports 15 onboard checks instead of 14.** The new `maintenance_loop_ticking` check reports `ok=true` with a `"skipped — maintenance_loop flag is OFF"` detail when the flag is off. Healthchecks asserting `total == 14` will need to move to `total >= 15`; healthchecks that consume `fully_passed` are unaffected.
- **First sync after flipping a `topology_v2_<connector>` flag re-walks the source** to emit per-source containers. Subsequent syncs ride the delta cursors and are fast.
- **First maintenance tick can take longer than subsequent ticks** if your index has accumulated months of orphan vectors. The `orphans_pruned` counter trends to single digits once the index has settled.

### Internal

- **Wave E topology v2 connector adoption.** Per-connector pilots ship for all 8 connectors in the fleet; the legacy single-cursor shim stays as the default until each pilot soaks against a real corpus.
- **KFEAT-021 Phase 1** — the proactive replacement for the reactive KFEAT-020 preflight check. Five sabotage proofs cover the orphan-vector pruning loop, retention window, flag-OFF inertness, and the stall-detection check.
- **F-rule discipline** — every new connector ships with the canonical test discipline (contract / integration both branches / e2e composed-path / BDD both branches), the F39 sensitivity routing locked at the boundary, and F54 both-branch coverage for every new flag.

### Things that haven't changed

- The legacy `collections:` + `agents:` blocks. Operators using them continue to work.
- The provider plugin selector (`provider:`). Same shape, same plugins.
- MCP tool names + JSON schemas. The 12 tools shipped in v2026.5.18 (plus `tool_features_status` from v2026.5.24a1) are unchanged.

## [2026.5.24a1] - 2026-05-23 — Topology v2 operator config + SharePoint connector + MCP cold-start fix (alpha)

> **Upgrading?** Nothing required — every new behaviour sits behind a default-off feature flag, so pulling this version is a structural no-op. To opt into the alpha (topology v2 + SharePoint), follow [`docs/upgrades/v2026.5.24a1.md`](docs/upgrades/v2026.5.24a1.md).

### New for operators

- **A new declarative shape for connectors, collections, scope profiles, and skills.** Add a `topology_v2:` block to your `kairix.config.yaml` and declare each connector instance, credential bundle, connector-credential pair, collection, per-actor scope profile, and skill as a first-class operator entity. Same shape `kairix config validate` reads, same shape the new `topology_v2_*` onboard checks read. The canonical example is at the repo root: [`kairix.config.example.yaml`](kairix.config.example.yaml). Gated by `topology_v2_config` — flag off means the block is parsed but inert. Cutover by editing the YAML, flipping the flag, and running `kairix worker apply-config`.
- **SharePoint connector** — pulls document libraries (PDF / DOCX / PPTX / XLSX) via Microsoft Graph drive-delta query and dispatches binaries through the existing kairix extractor registry. Reuses the M365 Azure AD app registration the email-headers + calendar siblings already use (one tenant id / client id / client secret triple, three secret names: `connector-m365-tenant-id` / `-client-id` / `-client-secret`). Gated by `connector_sharepoint` — flag off means the connector isn't loaded.
- **Obsidian per-folder routing** behind `topology_v2_obsidian`. When on, the connector emits one Container per top-level vault folder (each with its own delta cursor), and the hierarchy walk emits one FOLDER node per directory parent-before-child. When off, the connector keeps the legacy single-cursor behaviour.
- **Three new `kairix onboard check` checks.** `topology_v2_config_valid` parses + cross-references the `topology_v2:` block. `topology_v2_cc_pairs_registered` confirms each declared cc_pair has a row in `topology_cc_pairs`. `sharepoint_credentials_loaded` confirms the three M365 secrets resolve. Each check is gated by the same flag as the feature it covers — flag off means the check reports `ok=true` with a `"skipped"` detail, so a fresh install sees 14 checks pass (was 11) without any operator action.
- **MCP cold-start is no longer an adoption blocker.** The HTTP server returns HTTP 503 with `Retry-After` while the search pipeline is warming, and `/healthz/ready` is the single readiness source. Structured startup logs ship the warm-up event so you can grep your log shipper for the readiness transition. The previous cold-start envelope returned through tool responses keeps working for agents already wired to parse it.

### New for agents

- **The declarative agent-driven setup recipe** — `docs/getting-started/agent-driven-setup.md` covers what to ask the user, the minimal-viable YAML template, the secret name → env var name map for every connector, the 4-step validation pipeline, and the failure → fix table mirroring the F21 affordance shape. Optimised for unambiguous machine instructions so an LLM agent setting kairix up on a user's behalf has a clean recipe to follow.
- **Pip-install path lands alongside Docker** in the refreshed `docs/getting-started/quick-start.md`. Parallel sections for Docker (`docker compose up`) and pip (`pip install "kairix[agents]"` → `kairix worker run &` + `kairix mcp serve`). Each step has a verification command (`kairix onboard check`, `kairix worker preflight`, `kairix features status`) so an operator can confirm they're on the happy path.

### Internal

- **#305 namespace fix** — the six topology v2 blocks now nest under a single `topology_v2:` parent key instead of leaking into the top-level `collections:` namespace where they collided with the legacy v1 `collections.shared` dict.
- **F19 / F17 / F16 paydown** — 100 entries cleared from the unused-params / duplicated-literals / cognitive-complexity baselines, all by refactor (no rule exemptions).
- **KFEAT-018 currency check** — release-tag-gated assertion that the grandfathering paydown plan snapshot is current as of each release tag. Stops the paydown plan from drifting between releases.
- **KFEAT-019 Phase 1** — behaviour traceability matrix (135 rows, 31% MISSING baseline) — the inventory we'll burn down across the next several releases.
- **MCP cold-start parts 3 + 4** — structured startup logs ship the warm-up event with the readiness transition fields; the docs land in `docs/operations/MCP-DEPLOYMENT.md` with the affordance shape for log-shipper consumers.

### Behaviour changes worth noting

- **Default deploy reports 14 onboard checks instead of 11.** The three new checks each report `ok=true` with a `"skipped — <flag> flag is OFF"` detail when their feature flag is off. Any healthcheck that asserts `total == 11` will need to update to `total >= 14`; healthchecks that consume `fully_passed` are unaffected.
- **Existing Obsidian deployments are unchanged.** `topology_v2_obsidian` defaults to off — single-cursor scan behaviour is preserved bit-for-bit unless you flip the flag.
- **reflib NDCG@10 may dip 0.01–0.02** if you flip `connector_sharepoint` and point it at a real SharePoint corpus before KFEAT-015 lands per-collection ranker calibration. Re-run `kairix benchmark run` after a full embed pass to settle the new baseline.

### Things that haven't changed

- The legacy `collections:` + `agents:` blocks at the top level of `kairix.config.yaml`. Both continue to work; the new `topology_v2:` block is opt-in.
- The provider plugin selector (`provider:`). Same shape, same plugins, same secret-retrieval pattern per plugin.
- MCP tool names + JSON schemas. The 12 tools shipped in v2026.5.18 are unchanged.
- Authentication. kairix continues to have no built-in auth; deployments that put a gateway in front stay unchanged.

## [2026.5.18] - 2026-05-21 — Conversational memory shipping + one benchmark CLI + machine-actionable cold-start

> **Upgrading?** Nothing required. Legacy CLIs (`kairix eval`, `kairix probe`, `kairix soak`) keep working with a deprecation notice pointing at the new consolidated `kairix benchmark run`. Full upgrade notes: [`docs/upgrades/v2026.5.18.md`](docs/upgrades/v2026.5.18.md).

### New for agents

- **Conversational recall surfaces facts the way you'd expect.** When a question depends on a fact extracted from a conversation (e.g. "What is agent-alpha's role?"), the synthesiser keeps the structured fact triplet in context and the answer comes through with its citation. Short fact rows previously got dropped by a snippet-length filter aimed at sparse chunk text; that filter now applies to chunk-tier results only.
- **`warm` MCP tool** returns a machine-actionable cold-start envelope (`status: retryable_not_ready`, `error_code: KAIRIX_COLD_START`, `retry_after_ms`, `agent_instruction`) when retrieval isn't ready, so the agent knows to wait and retry rather than answer from memory. Call it at session start; subsequent retrieval tools return real results once warm-up succeeds.
- **Attribute-fact intent classifier covers two more question shapes**: `"what did X verb?"` and `"who is/was X's Y?"`. Both previously routed to a generic semantic intent and lost the fact-retriever weight they needed.
- **Facts carry session date metadata** via a new `evidence_at` field. Temporal questions resolve to absolute dates rather than relative phrases echoed from the source turns.

### New for operators

- **`kairix benchmark run`** is the consolidated entry point for retrieval-quality and answer-correctness measurement. One CLI; pluggable scorers (NDCG, Hit@K, MRR, LLM-judge, latency); `--mode` for single-shot now, concurrent and soak shipping in the next release; `--scope` / `--collection` / `--categories` / `--gates` / `--baseline` flags. Suite YAML extends backward-compatibly with `expected_answer:` for LLM-judge scoring alongside `gold_titles:` for NDCG.
- **`kairix eval` against a conversational corpus extracts facts by default** via the new `kairix.corpus.wiring` factory. Previously the eval CLI ran against a no-op fact extractor; conversational suites now produce real SQLite state. Reference-library NDCG suites are unaffected.
- **`KAIRIX_TRACE=1`** now configures a logging handler at CLI start, so the per-query diagnostic lines (`prep.context: chunks K/N kept, facts K/N kept, M ctx chars`) appear on stderr. Off by default; production stays quiet.
- **Eval scoring mirrors `kairix prep`.** `kairix eval` routes through the same `SearchPipeline` (`--via-prep`, default on). A scope-routing bug where `--scope` was silently overridden to `shared+agent` whenever `--agent` was set is also fixed.

### Internal

- **`kairix.corpus.ingest.ingest_corpus`** is now the shared ingest primitive. `kairix ingest-chat`, the eval CLI's `SuiteRunner`, and the LoCoMo benchmark harness all route through it. One ingest path, one set of behaviours; production and benchmark code paths can't diverge.
- **F30 fitness function** (new) — every CLI subcommand and MCP tool has at least one outcome test that exercises the subprocess path and asserts on captured output. Pre-existing surfaces grandfathered; baseline only shrinks.
- **F5 and F6 broadened.** F5 also catches attribute access to underscore-prefixed kairix names on imported modules. F6 also catches `*_loader`, `*_factory`, `*_builder`, `*_provider`, `*_resolver` test-injection seams in addition to `*_fn`. Methods on `ClassDef` (canonical Deps pattern) are exempted.
- **`safe-commit.sh`** generates `coverage.xml` and enforces the F7 per-file coverage floor locally, closing a parity gap with CI. Agents working on kairix get F7 feedback in seconds rather than minutes.

**Design context:** [`docs/architecture/fact-layer.md`](docs/architecture/fact-layer.md) — ADR covering the conversational memory architecture, the five capabilities, and the federation pattern. [`docs/upgrades/v2026.5.18.md`](docs/upgrades/v2026.5.18.md) — operator and agent upgrade guide.

### Security

- **Warm-state flag moved out of `/tmp` into the kairix data directory.** The cross-process readiness flag added in v2026.5.17 lived at `/tmp/kairix-warm.flag` — a world-writable, predictable path. The flag now lives at `<KAIRIX_DATA_DIR>/warm.flag`, owned by the kairix process user and not world-writable. The MCP server clears any stale flag at startup so a restarted container correctly reports not-ready until it has actually re-warmed.

## [2026.5.17] - 2026-05-18 — Faster searches under concurrent load + plug-in support for any LLM provider

> **Upgrading?** **One required config change**: add `provider: <name>` to the top of your `kairix.config.yaml` before pulling this version. Kairix won't start without it and will list the installed plug-in names so you can pick. See [`docs/operations/runbooks/how-to-upgrade-kairix.md`](docs/operations/runbooks/how-to-upgrade-kairix.md) for the one-line edit. **Operators with a custom `kairix.config.yaml` mounted into the container** should move to the new overlay pattern before pulling — it stops future required-setting bumps from quietly breaking your deploy. See [`docs/operations/runbooks/config-overlay-upgrade.md`](docs/operations/runbooks/config-overlay-upgrade.md). **New for agents**: searches feel faster when several agents work at the same time, and a cold container no longer returns "fetch failed" — kairix tells you it's warming and how long it needs. **New for operators**: the new `kairix probe-config` command checks your endpoint after setup and recommends concrete tuning; seven first-party plug-ins ship today (Azure Foundry, Azure Legacy, OpenAI direct, Bedrock, Ollama, LiteLLM proxy, Anthropic).

### New for agents

- **Searches feel faster under concurrent load.** Probe data on the reference-library suite shows mean latency at concurrency 10 dropped from 1452 ms to 804 ms across the dev cycle. (closes #281, #282, #287, #288)
- **Ten agents asking different questions at the same time pay one network round-trip.** Concurrent embed calls in a 50 ms window fold into one batched request. (closes #288)
- **A second ask for the same text comes back from cache.** Same text → same vector, regardless of which agent or scope asked. (closes #281, #285)
- **Vector lookups got 13× faster.** One batched database query instead of N+1 per result. (closes #287)
- **The first call to a cold kairix no longer fails.** Previously the first agent call against a just-restarted container could return "fetch failed" because the search pipeline hadn't finished warming. Now every agent-facing tool — bootstrap, brief, search, entity, prep, timeline, research, contradict, entity_suggest, entity_validate — returns a clear "kairix is warming, retry in ~N seconds" response while warm-up runs in the background. The second call returns real results.
- **Probe reports per-stage timings** for every query — so you can see where time goes. (closes #282)

### New for operators

- **`kairix probe-config`** — runs cold + warm + concurrent calls against your configured endpoint and emits a JSON report: status (healthy / degraded / unreachable), latencies, cache hit rate, and concrete tuning recommendations. Share the file with support if anything looks off. Privacy-safe: the report includes the endpoint hostname only — no full URLs, no credentials.
- **`kairix entity count` / `audit` / `purge`** — fast entity-graph readout plus cleanup tools for stale entries. (closes #259, #260, #261)
- **Embed connection pool is now tunable.** `KAIRIX_EMBED_POOL_SIZE` / `_KEEPALIVE_CONNECTIONS` / `_EXPIRY_S` match the pool to your team size and endpoint distance. (closes #280)
- **Release pipeline gates stable releases on alpha.** Every `v2026.5.17` cannot publish without a successful `v2026.5.17-alpha` first. Alpha tags auto-deploy to your alpha host via a Go-built webhook. (closes #272)
- **Search pipeline pre-warms at container start** so the first request doesn't pay the 192 MB factory-init tax. (closes #278, #279)
- **Benchmark resolves bundled suites by name.** `kairix benchmark run reflib` works from any directory. (closes #268)
- **`kairix --version` works inside Docker.** Build passes the version so reports show the real release. (closes #267)
- **Upgrades that add new required settings won't quietly break your deploy.** Kairix now ships its own complete config inside the Docker image; your host-side YAML only carries the keys you want to override (paths, agents, retrieval tuning). When a future release adds a required setting, it's already in the image — you don't have to remember to copy it forward. Overlays can also pin a minimum kairix version with `_schema_version_required_min`; if the image doesn't meet it, kairix refuses to start and tells you which release to upgrade to. The previous single-file pattern (`KAIRIX_CONFIG_PATH`) keeps working for existing deployments. Migration guide: [`docs/operations/runbooks/config-overlay-upgrade.md`](docs/operations/runbooks/config-overlay-upgrade.md).
- **`docker compose up --wait` actually waits for kairix to be ready.** The healthcheck now uses a new `kairix onboard ready` command that only succeeds once the first real agent call would too. Your deploy command returns when kairix is genuinely warm, not just when the port is bound.
- **Auto-deploys now actually restart the container.** Previously a failed startup health check could leave the old container running while the deploy log said "deployed" — diagnosed during the alpha-9 recovery. The webhook now fully recycles the container on every deploy (~10s slower; eliminates the trap).

### Internal (the foundation for multi-provider deployments)

- **Three-layer architecture** in [`docs/architecture/provider-plugin-architecture.md`](docs/architecture/provider-plugin-architecture.md). Domain (`kairix/core/`) talks to universal endpoint concerns (`kairix/transport/`: pool / retry / coalesce / cache / timeout) and per-endpoint plugins (`kairix/providers/<name>/`) only via Protocols.
- **Stops rebuilding the model connection on every batch.** Each concurrent embed batch was setting up a fresh TLS connection (~300-500 ms cold). The new `ClientPool` keeps one warm connection for the container's life.
- **F26–F29 fitness functions** lock the split: domain can't import transport or providers, no cross-provider imports, every plugin needs BDD coverage, performance code stays in `kairix/quality/probe/`.
- **Provider plugin discovery via Python entry points; config-yaml-driven selection.** Set `provider: <name>` at the top of `kairix.config.yaml` to select your plugin. Each plugin owns its own credential-retrieval pattern (Azure plugins use Key Vault; AWS plugins use Secrets Manager / IAM; etc.), so the operator doesn't wire secrets in the yaml. Existing deployments MUST add `provider:` to their `kairix.config.yaml` before upgrading (see [`docs/operations/runbooks/how-to-upgrade-kairix.md`](docs/operations/runbooks/how-to-upgrade-kairix.md)). Third parties ship `pip install kairix-provider-foo`. Seven first-party plugins available: Azure Foundry, Azure Legacy, OpenAI direct, Bedrock, Ollama, LiteLLM proxy, Anthropic. (closes #285)
- **F21–F25 quality rules** (actionable-feedback markers, path naming, README resolvers, no `tests.*` imports in production, every CLI has an MCP affordance).
- **Cognitive-complexity burndown** across chunker / reflib / sweep / entities / contradict / temporal — every flagged function now under 15. (closes #250)
- **MCP retroactively exposes safe read-only operational capabilities** — onboard check, capability introspection. (closes #277)
- **Performance + soak test suite** catches regressions like the embed-pipeline class in CI. (closes #276)
- **More test files moved to clean dependency injection.** Seventeen test modules stopped reaching into kairix internals and now inject fakes through the public interface — keeps tests honest as the code evolves so they catch real regressions instead of silently going stale.
- **New end-to-end coverage for classify and config-layering** — operator-language scenarios that exercise the same paths a real deploy goes through, including the exact alpha-9 failure mode.

### Dependency updates

- `docker/login-action` 3.7.0 → 4.1.0 (#290)
- `actions/download-artifact` 4.3.0 → 8.0.1 (#291)
- `actions/checkout` 4 → 6 (#292)
- `ruff` 0.15.12 → 0.15.13 (dev) (#293)
- `sentence-transformers` requirement widened `>=3.0,<4` → `>=3.0,<6` (#294)

### Fixed

- **Worker survives `SystemExit` from helpers.** Recall-gate alerts no longer crash the container. (closes #270)
- **MCP entity lookup checks aliases.** Lookups find the entity even when the canonical name differs from the alias asked for. (closes #253)
- **`prep` (L0 summary) returns grounded content.** No more generic responses when the knowledge store has the answer. (closes #254)
- **Eval module security hardening** — path confinement, prompt-injection guards, finite-score validation. (closes #143)
- **Per-collection retrieval overrides** apply to single-collection MCP and benchmark calls. (closes #274)
- **Reflib benchmarks captured per release** for quality regression tracking. (closes #271)
- **Retrieval health & recovery runbook** added for operators. (closes #252)
- **Python 3.14 + numpy + pytest-cov** import-order error worked around. (closes #211)

### Still open

- PVT MCP HTTP test harness (#284)
- SonarCloud check status mismatch (#269)
- Webhook auto-deploy on release (#286)

---

## [2026.5.15] - 2026-05-14 — Agent-first kairix + worker observability + F-rule legacy fully burned

> **Upgrading?** Drop-in. No public API breaks. **New for agents**: `kairix bootstrap <agent>` returns a session-start orientation envelope; every MCP tool now includes a `health` field that tells agents what's offline and what to do next; `kairix onboard check --json` is the canonical "is kairix working" probe with per-check remediation strings. **New for operators**: worker pause/resume + observable phase state + skip-on-idle maintenance. **Internal**: 7 fitness-function baselines burned to zero (F1, F3, F4, F5, F6, F7, F9); per-file coverage floor ratcheted 85% → 90%; 10 shim modules deleted; 22 env-var helpers centralised in `paths.py`/`secrets.py`.

### Added — agent affordance (#246)

- **`kairix bootstrap <agent>`** — single command returning the agent's session-start orientation: role, current `Board.md`, last N daily memory entries, active goals, and a `health` envelope. CLI: markdown by default, `--json` for tooling. MCP: `tool_bootstrap(agent, max_memory_days=3)`. Designed so even with vector search or chat offline, the bootstrap **still returns** board + memory and tells the agent what's degraded.
- **`KairixHealth` envelope on every tool response.** `tool_search`, `tool_brief`, `tool_entity`, `tool_bootstrap` all return a `health` field with `vector_search` / `bm25` / `chat` status, `secrets_loaded`, `degraded_reason`, and `next_action`. When kairix degrades, the response **still returns working-subsystem results** AND tells the agent what to do next (e.g. "Ask your admin to run `kairix onboard check`; results below are BM25-only"). New shared module: `kairix/core/health.py`; threaded timeout enforces a 2s probe budget.
- **Prescriptive MCP tool descriptions.** The `description=` string an LLM agent sees in its tool list is now a usage policy, not a definition. `tool_search`: "Call before answering any factual question about prior work…". `tool_bootstrap`: "Call at session start or whenever you switch topics…". `tool_brief`: "Call when you want a synthesised view…". `tool_entity`: "Call when you need facts about a specific named entity…".
- **`kairix onboard check --json`** + clean exit-code semantics. Exit 0 on full pass, 1 on any failure. JSON shape: `{passed, total, fully_passed, failures: [{check, detail, remediation}]}`. Each of the 9 checks has a canonical, operator-actionable remediation string (e.g. `secrets_loaded` failure → "Run `sudo systemctl enable --now kairix-fetch-secrets.service` on the host…"). Wired as the canonical docker-compose healthcheck.
- **`kairix-memory-prompt` openclaw plugin packaged canonically** at `kairix/plugins/openclaw/memory-prompt/`. Symlinked into the docker image at `/opt/kairix/plugins/openclaw/`. Calls `kairix bootstrap <agent>` at session start and `appendSystemContext`s the result so agents start oriented instead of reactive. Degraded fallback message when bootstrap fails — session start is never blocked.
- **`docs/agents/AGENT-SETUP.md`** + **`docs/agents/ADMIN-CONVERSATION.md`** — the operating contract an agent reads on first run, plus the script an agent follows when discussing kairix configuration with its admin human (symptom → exact words to say → concrete command). README quick-start rewritten in agent-first ordering: install → secrets → collections → verify → wire-into-agent.

### Added — operator affordance (#224, #222)

- **Worker pause/resume (#224 phase 4).** `kairix worker pause` / `kairix worker resume` toggle a touch-file in the data dir. The running worker enters PAUSED at the next loop iteration (within 5s) and stops doing task work until the flag is removed. Decoupled from the worker process — a stuck worker can still be paused, and the pause survives restarts.
- **`kairix worker status` (#224 phase 5).** Reads the persisted `WorkerState` JSON (atomic temp+rename writes) and prints phase, embedded total, failed chunks, recall alerts, restart count, uptime. Exit 0 if state file present, 1 if missing.
- **Worker skip-on-idle maintenance (#224 phase 2).** When `consecutive_embed_noops` ≥ 10 (env `KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD`), the worker stops running `entity_seed` / `health_check` / `wikilinks`. Resumes on the next embed that does work. Friendlier to shared hosts.
- **`kairix benchmark run <name>` resolves bundled suites by name (#222).** `kairix benchmark run reflib` finds the bundled `reflib-gold-v3.yaml`, reads `default_collection` from suite metadata, and runs scoped correctly — no more `--collection reference-library` tax for dogfooding. `kairix benchmark list` enumerates bundled suites with their default_collection and one-line description. Unknown suite name → exit 1 with `did you mean: kairix benchmark list?` hint.
- **F14 — `sonar.issue.ignore` entries require rationale comment.** Mirrors F3 for SonarCloud suppressions.
- **Scheduled baseline audit (`.github/workflows/baseline-audit.yml`).** Mondays 08:00 UTC + workflow_dispatch. Fails if any baseline entry is stale.
- **`scripts/checks/audit_baselines.py`** — local invocation of the audit logic.

### Changed (internal — quality-exceptions Wave 2-5 + F-rule legacy closure)

- **F-rule baselines closed.** Seven baselines went from grandfathered violations to **zero**:
  - **F1** (no `@patch` on kairix internals): 3 → 0. Refactored `tests/test_paths.py` to inject `platform=` instead of patching `kairix.paths.sys`; `tests/search/test_config_loader.py` driven by malformed YAML naturally; one weak smoke test deleted (behaviour pinned elsewhere).
  - **F3** (suppressions require rationale): 32 → 0. Every `# noqa` / `# NOSONAR` / `# pragma: no cover` / `# type: ignore` / `# nosec` carries an em-dash rationale.
  - **F4** (env reads centralised): 18 → 0. 22 new helpers in `paths.py`/`secrets.py`; every `os.environ.get("KAIRIX_*")` outside those two modules now routes through a typed helper.
  - **F5** (no internal-name imports in tests): 13 → 0. Promoted private helpers in `paths.py` and `core/search/config_loader.py` to public.
  - **F6** (no `*_fn=None` test-only kwargs in production): 12 → 0. AST detector extended to walk `ClassDef` `AnnAssign` fields (was only walking function params); surfaced 3 dataclass-field violations all refactored to `field(default_factory=lambda: _default_X)`. Eight `_*_defaults.py` shim modules deleted — their lazy-import bodies moved into the dataclass module as `_default_X` functions.
  - **F7 / F9** (per-file 85% floor, unit and union): 34 + 33 → 0. 23 production files lifted past 90%; 11 small files in the 85-90% band lifted to 97-100%. 90+ new sabotage-proven unit tests.
- **F2 baseline pinned at 8** — env-feature tests for paths/secrets/credentials/config_loader plus the autouse `no_azure_calls` safety fixture. These directly test the env-var-reading API; eliminating `monkeypatch.setenv` means changing what's tested. Net-new F2 violations still block.
- **F7/F9 floor ratcheted 85% → 90%**. `pyproject.toml`'s `fail_under` raised 80 → 88 (tracks current achievable floor).
- **F6 AST detector extended**. v1 walked `FunctionDef` / `AsyncFunctionDef` only; v2 also walks `ClassDef` `AnnAssign` so dataclass fields shaped `x_fn: Callable | None = None` are caught.
- **Audit script prefix fix.** `scripts/checks/audit_baselines.py` initially missed coverage-baseline matches because Cobertura's `filename` is source-root-relative. Fixed to read `<source>` and re-prepend.
- **Deterministic `fake_llm_backend.embed`** (#240). `hash(text) % 1000` had two failure modes (PYTHONHASHSEED + 0.1% modular collisions); replaced with `sha256(text)[:4]` truncated to a 2³² seed space.

### Removed

- **`tests/integration/test_mcp_tool_contracts.py`** — weakly-asserting smoke test (`"results" in result or "error" in result`) requiring `@patch("kairix._azure.embed_text", ...)`. Behaviour covered by `tests/use_cases/test_search.py` + `tests/integration/test_search_pipeline.py` + `tests/contracts/test_cli_mcp_parity_search.py`.
- **10 shim modules** — 8 `_*_defaults.py` use-case shims + `_pipeline_defaults.py` + `_timeline_defaults.py`. Bodies inlined into their dataclass modules.

### Issues filed / closed

- **Closed**: #246 (agent-first kairix — bootstrap + prescriptive MCP descriptions + health envelope + structured onboard check + plugin packaging + docs), #240 (flaky embed fake), #224 (worker resource discipline — phases 1, 2, 4, 5, 6 shipped; phase 3 deferred via #243), #222 (benchmark UX defaults), #203 (Wave 5 ratchet), #244 (F6 detector gap + refactor), #193 (quality-gate exceptions umbrella), #198 (F7 coverage backfill), #200 (F2 monkeypatch elimination), #201 (F1+F5 in-test internals).
- **Filed**: #242 (SonarCloud project still keyed under the previous org after the GitHub org rename — needs SonarCloud admin to update the GitHub binding), #243 (SRE/platform-health worker — design-first; recurring `kairix-fetch-secrets.service` disabled incident is now a concrete user story on that issue).

### Operational notes

- **Agents**: at session start, call `kairix bootstrap <your-agent>`. If `health.vector_search != "ok"`, surface that to your human and use BM25 results — don't silently fail.
- **Admins**: `kairix onboard check --json` is the canonical health probe. Wire into your docker-compose healthcheck and any external monitor.
- **`kairix worker status`** exit code is the authoritative "worker alive AND has run" signal. State file: `${KAIRIX_DATA_DIR}/worker-state.json`.
- **Shared hosts**: tune `KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD` (default 10) and apply the resource caps from `docker-compose.example.yml`.
- **SonarCloud PR scans show red until #242 admin step lands.** Branch protection on `develop` does not require SonarCloud, so merges are unaffected.

---

## [2026.5.14] - 2026-05-14 — FTS rebuild atomicity + quality-exceptions Wave 1 burndown

> **Upgrading?** Drop-in. The FTS fix prevents transient `no such table: documents_fts` errors during worker rebuild cycles — operators occasionally seeing those in logs will stop seeing them. Internal refactors land alongside; no public-API changes.

### Fixed

- **`documents_fts` no longer disappears mid-rebuild (#223).** `rebuild_fts()` ran `DROP TABLE`, `CREATE VIRTUAL TABLE`, `INSERT` as three separate auto-commit operations. Python's `sqlite3` default isolation doesn't auto-begin a transaction for DDL, so the DROP committed immediately. Any concurrent reader querying `documents_fts` between the DROP and the subsequent `CREATE`/`INSERT`/`commit` saw "no such table" and BM25 silently fell back to vector-only. The rebuild is now wrapped in `BEGIN IMMEDIATE` / `commit` (or honours an existing caller transaction). Atomic from any reader's perspective. Two regression tests pin the property; one is sabotage-proven.

### Changed (internal — quality-exceptions Wave 1)

- **F6 baseline emptied (12 → 0).** All `*_fn=None` test-only kwargs in production are gone, replaced by typed `*Deps` dataclasses with `default_factory` factories. Modules converted across PRs #209, #213, #215, #212: `quality/benchmark/runner.py`, `agents/briefing/pipeline.py`, `agents/research/{graph,nodes}.py`, `core/search/config_loader.py`, `knowledge/contradict/detector.py`, `knowledge/summaries/generate.py`, `platform/llm/backends.py`, `platform/onboard/check.py`, `platform/setup/wizard.py`, `quality/eval/retrieval.py`, `worker.py`.
- **`EmbedDependencies` refactored to the `default_factory` pattern (#216, refs #204).** Eliminates the `Optional[Callable] | None = None` + `__post_init__` self-resolution that caused mypy `--strict` regressions and required `assert deps.X is not None` ladders at every call site. 6 such assertions deleted from `embed.py`. The new `kairix/core/embed/_deps_defaults.py` sibling module is the canonical pattern; same shape can roll forward to `SearchDeps`, `SummariesDeps`, `LLMJudgeScorer`.
- **Coverage backfills (PRs #205, #206, #207, #210):** `rerank.py` 62 → 100%, `vector_repository.py` 0 → 100%, `embed.py` 80 → 100% testable surface, `recall_check.py` 7 pragmas → 0. F7 baseline -4. Dead `FileNotFoundError` guard in `recall_check.py` removed (rationale was self-contradictory; helpers it claimed to defend against don't raise FNF).
- **Dependency bumps**: `openai` requirement widened to `>=1.40,<3`; `codecov/codecov-action` 5 → 6, `docker/setup-buildx-action` 3 → 4, `SonarSource/sonarqube-scan-action` 6 → 8, `dorny/paths-filter` 3 → 4, plus pinned-SHA updates to `pypa/gh-action-pypi-publish` and `hynek/build-and-inspect-python-package`.

### Operational notes

- **Reflib benchmark verification (2026-05-13):** post-fix reflib-gold-v3 scored 0.872 weighted / 0.944 NDCG@10. Pre-fix steady-state was 0.890 — the 0.018 delta is within Azure embedding stochasticity. The fix's value is preventing the transient BM25-blackout window during worker rebuilds, not a steady-state lift. Both runs ran with zero FTS errors.

---

## [2026.5.10] - 2026-05-10 — Worker stability, layered health probes, deploy self-heal

> **Upgrading?** Drop-in. The worker now survives recall-gate alerts (which were silently killing the process before this release). The `/healthz/ready` endpoint is new but additive — `/healthz` is unchanged for back-compat with existing load-balancer probes. systemd-managed deployments should adopt the example unit files in `scripts/install/` to fix the post-reboot self-heal gap (#167).

### Fixed

- **Worker no longer dies on recall-gate alerts.** The worker called the embed CLI, which used `sys.exit(1)` to signal a recall-gate degradation. `SystemExit` is not caught by `except Exception`, so every gate alert killed the worker container — Docker restart-looped it forever. The worker now calls `run_incremental_embed_pipeline()` (a new use case in `kairix/core/embed/use_cases.py`) directly, receives a structured `EmbedPipelineResult` dataclass, and treats recall-gate failures as logged alerts rather than fatal exits. Failed chunks, gate alerts, and unexpected exceptions are all logged; the worker continues to the next interval. (resolves the v2026.5.9 dogfood report)
- **Recall canary queries now persist across runs.** Pre-fix, the recall gate sampled five random documents per run and compared the new score to the previous run's — but the previous run had sampled five different documents. The "delta -60%" alerts were comparing apples to oranges. Queries are now persisted to `~/.cache/kairix/recall-canaries.json` on first build and reused on every subsequent run, so the run-over-run delta is meaningful. Operators can force a re-sample with `kairix embed --rebuild-canaries` after a major corpus change.

### Added

- **`/healthz/ready` — layered readiness probe.** Resolves the #167 gap where `/healthz` reported `ready=true` while vector search was silently broken because `/run/secrets/kairix.env` had never been hydrated after a reboot. The new endpoint reports per-capability detail (`secrets_loaded`, `vector_search_capable`, `bm25_search_capable`) plus a `detail` map of failure reasons. `/healthz` is unchanged. Wired into the production MCP server via `kairix/agents/mcp/capability_probe.py`. See `docs/operations/MCP-DEPLOYMENT.md`.
- **Deploy hygiene artifacts** in `scripts/install/`:
  - `kairix.service.example` — systemd unit with the correct `Requires=`/`After=` ordering against `kairix-fetch-secrets.service` and `docker.service`. Pre-fix, kairix.service could start before secrets were hydrated.
  - `kairix-fetch-secrets.service.example` — oneshot that hydrates `/run/secrets/kairix.env` from Azure Key Vault on every boot (since `/run` is tmpfs and clears on reboot).
  - `permissions-preflight.sh` — idempotent `ExecStartPre=` script that fixes `.env` ownership/mode mismatches (the #167 root cause), verifies the secrets file is non-empty, and confirms the merged environment has all required keys before docker compose touches anything.
- **`kairix embed --rebuild-canaries`** flag — discards the persisted canary suite and re-samples from the corpus. Use after a major index rebuild or corpus migration.

### Changed

- **`run_recall_gate()` accepts `rebuild_canaries=`** kwarg for the new flag.
- **`RecallChecker.check()` accepts `canary_cache_path` and `rebuild_canaries`** kwargs. `canary_cache_path=None` disables persistence (used by tests for adaptive-sampling exercise without polluting `~/.cache`).
- **Worker logs structured outcomes.** Embed completion now reports `embedded=N failed=N recall=X%` rather than just "embed complete"; failed chunk counts and recall alerts surface as warnings.

### Operational notes

- Existing systemd installs should diff their unit files against the new examples in `scripts/install/`. The principal change is `Requires=kairix-fetch-secrets.service` plus the `ExecStartPre=` hook. Migration is a copy-paste on the host; no kairix data migration required.
- Existing `~/.cache/kairix/recall-canaries.json` does not exist on already-deployed instances; the file is built lazily on the next embed run, so no operator action is required.

---

## [2026.5.9] - 2026-05-10 — Schema, security, onboarding, configurable scope, paths-DI pilot, fitness harness

### Added

- **Architecture fitness function harness (F1–F13)** — thirteen blocking quality gates wired into pre-commit, `safe-commit.sh`, and CI (Stage 0 / 2 / 5). Each gate uses ratcheting baselines: pre-existing violations are grandfathered in `.architecture/baseline/`, but a single net-new violation blocks the commit/PR. Detects forbidden patching of internal code (F1), env-var monkeypatching in tests (F2), un-rationalised suppressions (F3 — covers `# noqa` / `# NOSONAR` / `# pragma: no cover` / `# type: ignore` / `# nosec`), env-var reads outside `paths.py`/`secrets.py` (F4), private-name imports in tests (F5), `*_fn=None` test-only kwargs in production (F6), files below 85% line coverage on the unit run (F7), unmarked `test_*` functions (F8), files below 85% line coverage on the unit-∪-integration union (F9 — Stage 5 holistic, per Ford / Sadalage / Kua's *Building Evolutionary Architectures*), un-rationalised CI workflow silencers (`continue-on-error: true`, `fail_ci_if_error: false`) (F10), un-rationalised test skip mechanisms (`pytest.mark.skip`/`skipif`/`xfail`/`importorskip`) (F11), BDD features with no happy-path scenario (F12), and BDD scenarios that leak implementation symbols (F13). Canonical reference: [`docs/architecture/fitness-functions.md`](docs/architecture/fitness-functions.md).
- **Codecov properly wired** — `codecov.yml` declares carryforward for `unit` (Stage 2) and `integration` (Stage 3) coverage flags so the dashboard merges stages instead of conflating them. Patch target = 85% mirrors F7's per-file floor. Five components (Search / Agents / Knowledge / Quality / Core) provide per-area regression tracking. JUnit XMLs from contracts, unit, and integration jobs upload via `codecov/test-results-action@v1`, enabling Test Analytics for flaky-test and slow-test tracking. The `[tool.coverage.run].omit` list in `pyproject.toml` remains the single source of truth for excluded files (no parallel `ignore:` block in `codecov.yml`). Bundle analysis is intentionally not wired (Python-only project).
- **`uv.lock` shipped with the source tree** — pinned, hash-verified dependency resolution. CI installs and Docker image bakes resolve identically across runs; the same source tree always pulls the same package versions. Operators can audit the exact wheel SHAs they're consuming.

### Changed

- **`build_search_pipeline()` now applies the YAML retrieval section at startup** — the factory reads `kairix.config.yaml`'s top-level `retrieval:` block (and per-collection `retrieval:` overrides for single-collection queries) when called without an explicit config, instead of always using the sweep-tuned defaults. Operators who configured per-collection retrieval in v2026.5.7 will now see their tuning actually applied to MCP and benchmark calls. (#112)
- **`kairix benchmark run --collection X` honours `X.retrieval` overrides** — single-collection benchmarks merge X's `retrieval:` block over the global config before running. Reflib benchmark scores now match the documented baseline; previously the runtime silently ignored reflib's own tuned settings. (#112)
- **`scope=all-agents` / `scope=everything` fail loudly when no agents are configured** — the MCP and CLI search paths now return a structured error envelope (`"scope=all-agents / scope=everything requires an AgentRegistry with at least one agent registered..."`) when `kairix.config.yaml` has no `agents:` section. Previously the request silently returned `reference-library` hits. Operators relying on the old behaviour should add an `agents:` section or scope queries to `shared+agent` (the default). (#164)
- **Legacy `agents: [{collection: <name>}]` YAML emits a deprecation warning** — pointing at the multi-path `paths:` schema introduced in v2026.5.7. Behaviour unchanged; one warning per legacy agent at startup. (#115)

### Fixed

- **BM25 backend distinguishes `collections=[]` from `collections=None`** — explicit empty-list scope returns no results (search nothing); `None` searches all active documents (no filter). The previous conflation was the proximate cause of the silent fall-through under `scope=all-agents`. (#164)
- **Sweep BM25 weights validated finite and positive at entry** — `kairix eval sweep` rejects nan / inf / non-positive weight inputs with a `ValueError` before opening the database, instead of letting them reach the SQL `ORDER BY` where they'd produce nondeterministic ranking. (#143 Phase 0b)

### Roadmap

- **CLI / MCP feature parity initiative** ([#168](https://github.com/quanyeomans/kairix/issues/168)) — every kairix feature exposed via both CLI and MCP with uniform UX. Audit identified 8 surface gaps and 1 code-path divergence (timeline). Targeted for next sprint. Full design at [`docs/architecture/cli-mcp-feature-parity.md`](docs/architecture/cli-mcp-feature-parity.md).

## [2026.5.7] - 2026-05-07 — Configurable scope, agent provenance, eval gate, security hardening

> **Upgrading?** Drop-in. The `agent_owner` column is added via additive `ALTER TABLE` on container start; legacy `agents:` YAML keeps parsing. The new `in_default: bool` flag on collections defaults to `true`, so existing yamls behave identically to before. To use the new flag, set `in_default: false` on collections you want excluded from default search (typically `reference-library`, `archive`).

### Added

- **`in_default: bool` on each collection** — operators control which collections participate in default search scopes from yaml. Collections with `in_default: false` remain indexed and reachable via explicit `--collection <name>`; they don't auto-join `shared` / `shared+agent` / `all-agents` / `everything`. Replaces a hardcoded reflib carve-out. (#135)
- **`agent_owner` column on `documents`** — per-document agent provenance. Idempotent migration; existing rows are NULL. The embed scanner now tags new rows with the owning agent. (#114)
- **Multi-path `AgentDef` schema** — agents declare a list of read paths in `kairix.config.yaml`; out-of-the-box agents default to `/data/workspaces/{name}`. Old single-`collection:` YAML keeps parsing for one release window. (#115)
- **`kairix eval gate`** — quality-gate CLI that turns benchmark output into a go/hold verdict with concrete tuning recommendations. Closes the onboarding flow: `setup → entity seed → eval auto-gold → eval tune → eval gate`.
- **Operator runbooks** — `runbook-vector-search-failure`, `runbook-embedding-lag`, `how-to-rebuild-entity-graph`, `how-to-configure-pypi-trusted-publisher`. All parameterised against `KAIRIX_*` env vars; no operator-specific paths.
- **`paths: KairixPaths` injection on the wikilinks surface** — `injector` / `audit` / `cli` accept paths as an explicit dependency. First step in a broader paths-DI initiative; existing callers unchanged. (#140)
- **Per-collection `retrieval:` block on `reference-library` in the shipped example yaml** — replaces a hardcoded retrieval baseline. Operators get the known-good values by default and can deviate when they want to. (#135)

### Changed

- **`DefaultCollectionResolver` refactor** — the resolver no longer knows anything about specific collection names. The "is this in default scope?" predicate moved onto `CollectionsConfig` itself. Cyclomatic complexity per method ≤ 3. (#135)
- **`CollectionsConfig` is frozen** — `tuple[CollectionDef, ...]` instead of `list`; only the predicate methods are public. (#135)
- **Strict bool coercion on `in_default`** — non-boolean yaml values raise `ConfigValidationError` naming the offending key, rather than silently coercing `"false"` (a truthy string) to `True`. (#135)
- **`AgentRegistry.collections_for(name)`** is the new multi-collection accessor; `collection_for(name)` remains for legacy callers.
- **Wikilinks injector** reads paths lazily instead of at module import — removes the long-standing `importlib.reload` requirement from the test fixture. (#129)

### Removed

- Hardcoded `_RESERVED_COLLECTIONS = {"reference-library"}` carve-out in `DefaultCollectionResolver`. Replaced by the operator-yaml `in_default` flag. (#135)
- Hardcoded `if target == "reference-library":` retrieval-config branch. Replaced by per-collection `retrieval:` overrides. (#135)

### Fixed

- Benchmark crash when gold titles look like ISO dates — coerced at suite-load boundary. (#103)
- Bundled `reflib-gold-v1.yaml` unrunnable; removed in favour of `reflib-gold-v3.yaml`. (#104)
- `recall_check._embed_query` now goes through `EmbedProvider` for retry / rate-limit / backoff parity with the rest of the embed pipeline. (#43, OPS-007)
- MCP `tool_timeline` returned empty placeholders for non-temporal queries; the result-shape dereference is fixed. (#119)
- Schema migration on legacy DBs: `create_schema()` now runs `migrate()` between table-creation and index-creation, so the `idx_documents_agent_owner` index doesn't try to fire before the column exists. Regression test added. (#133)

### Security

- **2 BLOCKER path-traversal vulnerabilities** (SonarCloud S2083) cleared with documented CLI trust-boundary rationale. (#121)
- **23 MEDIUM hotspots** triaged with explicit per-finding rationale (ReDoS on bounded-input regexes, weak-cryptography on non-security `random.*`, Dockerfile permissions). (#128)
- **26 LOW hotspots** triaged; 2 real script-default fixes, the rest documented as false positives or deferred to a supply-chain hardening sprint. (#129)

### Migration

Drop-in upgrade. To benefit from `in_default`, set `in_default: false` on collections you want excluded from default search and restart the kairix containers. The deploy was UAT'd on the dogfood VM before this release tag was cut.

## [2026.5.3] - 2026-05-04 — MCP availability, agent bug closure, scope semantics

> **Upgrading? Read [`docs/upgrades/v2026.5.3.md`](docs/upgrades/v2026.5.3.md) first.** It tells your agents (or you) exactly what to change. The TL;DR is: **swap `/sse` to `/mcp` in your MCP client config.** No auth changes, no tunnels.

### Added
- **Streamable HTTP transport at `/mcp`** — every MCP tool call is now a normal HTTP request/response. Stateless per-request, no idle-connection failure mode. The legacy `/sse` endpoint is preserved on the same port for back-compat; clients can migrate at their own pace.
- **`/healthz` endpoint** — reflects readiness. Tool calls during cold-start return a structured `{"error": "kairix-initializing", "retry_after_ms": 1500}` instead of crashing.
- **Typed `Scope` parameter on every retrieval tool** — `search`, `prep`, `timeline`, and `contradict` accept five values: `shared`, `agent`, `shared+agent` (default), `all-agents`, `everything`. Cross-agent synthesis via `scope=all-agents` is now a first-class operation.
- **Agent registry** — `kairix.config.yaml` accepts an `agents:` section that declares which agents exist. `scope=all-agents` resolves to the union of their collections. Default per-agent path is `/data/workspaces/{agent}` when not declared explicitly.
- **`kairix config validate`** sub-command — catches missing collection names, duplicate agent definitions, overlapping write paths, unknown retrieval-override keys before they hit production.
- **`docs/operations/MCP-DEPLOYMENT.md`** — operator deployment guide.
- **`docs/operations/MCP-CLIENT-MIGRATION.md`** — client-side migration guide with per-client steps for Claude Desktop, Claude Code, OpenClaw, and custom Python/Node clients.
- **`docs/upgrades/v2026.5.3.md`** — version-specific upgrade guide. Drop into your agent's reading list for self-managed migrations.
- **Search log fields** — `agent`, `scope`, `collections_searched`, `vec_failed` added to the JSONL event schema.

### Changed
- **Container entrypoint** — `--transport http` (canonical) instead of `--transport sse` (deprecated alias).
- **Bundled `docker-compose.yml`** — host port now binds to `127.0.0.1` only by default. Kairix has no built-in auth; operators who want external access drop the prefix and put a gateway with auth in front.
- **`mcp` package floor** — `>=1.20,<2` (was `>=1.0,<2`) for streamable-HTTP transport stability. Other dependencies unchanged.
- **`contradict` default threshold** — `0.45` (was `0.6`) to match the new three-category composite scoring (direct + overstatement + status-mismatch). Result objects carry a `category` field. Saved invocations with explicit `--threshold 0.6` still work.
- **MCP error envelope** — uncaught exceptions inside tool handlers return `{"error": "<ExceptionClass>: <message>"}` instead of being masked as JSON-RPC `-32602 Invalid request parameters`. If you have retry logic on `-32602`, update it.
- **`tool_timeline` MCP behaviour** — falls through to search when the query has no temporal expression, matching CLI behaviour. Returns `is_temporal: false, fell_back: true`.

### Fixed
- **Research confidence always 0.0** — `mcp-kairix__research` now returns real confidence values. Previously `json.loads()` failed silently on prose responses; the new parser chain handles JSON and prose.
- **Briefing `--memory-root` path-doubling** — regression test guards against the failure mode and emits a warning if the override path already includes `/{agent}/memory`.
- **Entity suggest type errors** — role phrases are dropped, mistyped entities corrected via override sets, missing organisations promoted via configurable allowlist.
- **`-32602` masking real tool errors** — see Changed above.

### Architecture
- Eight new domain Protocols, each with a public Adapter, and a typed `Scope` enum closes Primitive Obsession. See `docs/architecture/ENGINEERING.md` §10 for the catalogue.
- Both pre-existing private-import test debts closed (`_collections_for`, `_parse_llm_response`).

### Tests
- **2,101 unit/contract/bdd tests**, **58 integration tests**. mypy strict clean across 167 source files. bandit clean on changed paths.

### Known incomplete (tracked)
- **#112** — kairix.config.yaml `retrieval:` section not loaded by the factory at runtime.
- **#114** — embed-side `agent_owner` chunk tagging.
- **#115** — multi-path agent collections schema (drops the hardcoded vault path; richer per-deployment customisation).
- **#116** — `prep` L0/L1 source non-determinism investigation.
- **#117** — user-vault gold-suite rebuild after document movement.

## [2026.4.27] - 2026-04-27 — Reference library gold suite, Docker-first deployment

### Added
- **160-query reference library gold suite** — curated benchmark covering all six query categories against the open-source reference library. Reproducible scores without a private knowledge store.
- **OpenAI SDK embed client** (#43) — `OpenAIEmbedProvider` using the `openai` SDK for direct OpenAI API embedding (non-Azure).
- **Multi-collection support** — `hybrid_search()` accepts multiple collection names; results fused across collections.
- **Port auto-detection** — `kairix mcp serve` and `kairix setup` auto-select an available port if the default is in use.

### Changed
- **Docker Compose is now the primary deployment method** — `docker compose up -d` replaces pip install as the recommended path. pip install remains as an alternative.
- **Benchmark scores updated** — weighted R10=0.8171, NDCG@10=0.8385, Hit@5=0.9629, MRR@10=0.7614 (160-query reference library suite).

### Tests
- **1,634 tests**, 86% coverage. Up from 1,222 at v2026.4.24a3.

## [2026.4.24a3] - 2026-04-24 — Researcher Agent, Embed SDK, security hardening

### Added
- **KFEAT-009: Self-contained storage** — removed QMD (Node.js) dependency entirely. Kairix now owns its own SQLite database, FTS5 full-text index, and sqlite-vec vector store. `pip install kairix` is the only install step.
- **BM25-primary fusion** — new default search strategy. BM25 results are ranked first; meaning-based (vector) results are appended for recall. 38-configuration sweep showed this outperforms standard RRF by +17% on weighted NDCG.
- **Configurable fusion strategy** — `RetrievalConfig.fusion_strategy` accepts `"bm25_primary"` (default) or `"rrf"`. Factory methods for common corpus types: `defaults()`, `for_semantic_corpus()`, `for_technical_documentation()`.
- **`kairix eval hybrid-sweep`** — grid search over fusion strategies, RRF constants, and boost parameters against a gold suite. Embedding cache for 60% faster iterations.
- **`kairix eval build-gold`** — TREC-style pooling + LLM judge to create unbiased relevance judgments from your own data.
- **`kairix eval sweep`** — BM25 column weight and query style optimisation.
- **KFEAT-010: MCP affordance** — budget auto-inference (entity lookups get smaller budgets, research queries get larger ones), entity-first hints in search results, plain-language tool descriptions.
- **KFEAT-004: Researcher Agent** — LangGraph state machine for iterative search. 6 nodes: classify_intent, retrieve, evaluate_sufficiency, refine_query, synthesise, give_up. Searches multiple times, refining the query until it finds a good answer or reports what's missing. Max 4 turns. New MCP tool: `tool_research()`.
- **EmbedProvider protocol** — `EmbedProvider` interface with `AzureEmbedProvider` and `OpenAIEmbedProvider` implementations using the `openai` SDK. Built-in retry, rate-limit handling, and exponential backoff. Factory: `get_embed_provider()`.
- **Public API surface** — `kairix.hybrid_search`, `kairix.SearchResult`, `kairix.RetrievalConfig`, `kairix.QueryIntent` exported from `kairix/__init__.py`.
- **`bm25_primary_fuse()`** in `rrf.py` — new fusion function for BM25-primary strategy.
- **Dependencies** — `langgraph>=0.2,<1` and `openai>=1.40,<2` added to core.

### Changed
- **README completely rewritten** — value-first messaging, plain language, cost comparison, agent platform integration context.
- **Benchmark scores updated** — weighted NDCG 0.818, NDCG@10 0.803, Hit@5 91.1% (293 queries, independent gold suite).
- **Vector default K** increased from 10 to 20 for better recall.
- **`RetrievalConfig`** now includes `fusion_strategy` and `rrf_k` fields.
- **Tool docstrings** rewritten for grade 8 reading level (plain language first, technical terms in brackets).
- **`CATEGORY_WEIGHTS`** centralised in `eval/constants.py` (was defined in 4 files with silent divergence).
- **`canonical_path()`** extracted to module level in `rrf.py` (was duplicated 3 times).
- **Multi-hop search** extracted from `search()` into `_run_multi_hop()` helper (reduces `search()` from 390 to ~320 lines).

### Fixed
- **Category alias bug** — sweep scoring now correctly maps `semantic→recall` and `keyword→conceptual`. Was dropping 40% of weighted score.
- **Cypher injection** — `GraphEdge` labels validated against `NodeLabel` enum via `__post_init__`.
- **Graph traversal DoS** — `max_hops` clamped to [1, 5].
- **MCP error leakage** — `str(exc)` no longer returned to callers; sanitised messages instead.
- **Secrets path leakage** — `OSError` messages no longer include internal file paths.
- **SSE transport** — MCP server defaults to `127.0.0.1` (was implicit `0.0.0.0`).
- **Lockfile** — moved from world-writable `/tmp` to `~/.cache/kairix/`.
- **Duplicate KV fetch** — `summaries/cli.py` now uses `kairix.secrets.get_secret()`.
- **Hardcoded legacy paths** — `benchmark/cli.py` QMD path replaced with `get_db_path()`.

### Removed
- **QMD dependency** — no more Node.js, npm, or external binary discovery.
- **`kairix/_qmd.py`** — QMD binary discovery module.
- **`qmd_azure_embed`** — backward-compatibility shim package.
- **`AnthropicBackend`** — stub that raised `NotImplementedError` on all methods (LSP violation).

### Security
- Dependency upper bounds added: `requests<3`, `httpx<1`, `pyyaml<7`.
- `SQLITE_VEC_PATH` no longer required; extension loaded via pip package.

### Tests
- **1,222 tests** (up from ~1,050 at v2026.4.18). 1,090 carry `@pytest.mark.unit`.
- New: 22 Researcher Agent tests, 25 MCP affordance tests, 8 EmbedProvider tests, 7 contract conformance tests, 5 e2e pipeline tests, 4 chunk-date enrichment tests.
- Dead QMD e2e test replaced with kairix pipeline e2e.

## [2026.4.18] - 2026-04-18 — kairix eval: automated evaluation suite generation

### Added
- **`kairix eval generate`** — GPL-inspired automated benchmark suite generation. Samples documents from the corpus, prompts gpt-4o-mini to write retrieval queries, runs hybrid search, judges retrieved documents with graded relevance (0/1/2), and outputs a suite YAML. Based on Generative Pseudo Labeling (Wang et al. 2022, NAACL).
- **`kairix eval enrich`** — converts an existing suite's `gold_path`-based cases to graded `gold_titles`. Runs hybrid search and LLM judge for each case. Preserves all other case fields.
- **`kairix eval monitor`** — canary regression detection with rolling JSONL log. Flags when weighted NDCG drops >5% vs the 7-day rolling average. Exit code 2 on regression (distinct from exit code 1 hard failure). Designed for integration after `kairix embed`.
- **`kairix eval report`** — generates a markdown trend report from the monitor log.
- **`kairix/eval/judge.py`** — per-document LLM relevance judge (gpt-4o-mini, 0/1/2 rubric, position-bias shuffle, 15-anchor calibration with `JudgeCalibrationError`).
- **`docs/evaluation/evaluation-methodology.md`** — methodology with research citations: Cranfield paradigm, GPL, TREC-DL, position bias (Arabzadeh et al. 2024), NDCG formula.
- **`docs/user-guide/eval-guide.md`** — user quickstart, command reference, monitoring setup, troubleshooting.

### Fixed
- Deployment process now uses tagged releases (`@v0.9.3`) rather than `@main` to make explicit which version is installed. `pip install git+...@main` silently skips reinstall when the version string is unchanged.

## [0.9.2] - 2026-04-15 — NDCG@10 in benchmark CLI output

### Changed
- **Benchmark CLI: NDCG@10 now shown in run summary** — `kairix benchmark run` now prints `NDCG@10`, `Hit@5`, and `MRR@10` directly below the weighted total when `ndcg`-scored cases are present in the suite. Previously these metrics were computed and stored in the result JSON but never displayed. NDCG@10 is the recommended metric for cross-run comparison; the weighted total continues to drive phase gate pass/fail logic.
- **Benchmark CLI: NDCG@10 delta in compare output** — `kairix benchmark compare A.json B.json` now shows a `NDCG@10 delta` row when both result files contain ndcg scores.
- `EVALUATION.md` — updated "Running the benchmark" section to show sample CLI output and clarify that NDCG@10 is the number to track across releases.

## [0.9.1] - 2026-04-15 — Apache 2.0, title-based qrels, Neo4j install script, deployment hardening

### Added
- **Benchmark: title-based document identity (TREC qrels pattern)** — `BenchmarkCase` now accepts `gold_title` (str) and `gold_titles` (list of `{title, relevance}` dicts) as the primary document identity for relevance judgments. Gold titles are stable note filename stems, decoupled from filesystem paths. A retrieved document matches if its filename stem normalises to the gold title, meaning benchmark scores are unaffected by vault reorganisation (files moved, folders renamed). New runner helpers: `_normalise_title()`, `_stem_from_path()`, `_title_in_retrieved()`, `_ndcg_score_by_title()`, `_hit_at_k_by_title()`, `_reciprocal_rank_by_title()`.
- **Benchmark: backwards compatibility** — existing suites using `gold_path`/`gold_paths` continue to work without modification. Path-based matching is retained as a fallback when `gold_titles`/`gold_title` are absent.
- **`kairix[neo4j]` optional dependency group** — `pip install "kairix[neo4j]"` installs the Neo4j Python driver (`neo4j>=5.0,<6.0`). Previously required a manual `pip install neo4j` step after deploy.
- **`check_secrets_loaded` two-tier check** — the deployment health check now probes the secrets file directly if env vars are absent. If the file exists and contains the required keys, the check returns OK with a note that credentials will activate on the next search call. This eliminates the false-negative on working deployments where secrets load lazily via `kairix._azure` import.
- **`scripts/install-neo4j.sh`** — Neo4j Community Edition install script. `--docker` (default): writes a minimal docker-compose.yml and starts `neo4j:5-community`. `--apt`: adds the Neo4j apt repository and installs via systemd. Both options print a GPL3 licence notice before proceeding, run `kairix onboard check` on completion.
- **`check_neo4j_reachable` improved fix hint** — now includes a `scripts/install-neo4j.sh` reference and a `docker run` one-liner for quick starts. Clarifies Neo4j is optional — entity boost and multi-hop are degraded without it.
- **`tests/onboard/test_check.py`** — deployment health check tests: Neo4j fix hint content assertions, secrets two-tier probe, vault root config, `run_all_checks` structural tests.

### Changed
- **Licence: MIT → Apache 2.0** — adds patent grant language. Better for commercial adoption and open-source ecosystem compatibility. `LICENSE` file replaced with full Apache 2.0 text. Copyright 2024-2026 quanyeomans contributors.
- `suites/example.yaml` — all cases migrated from `gold_paths` (path-based) to `gold_titles` (title-based). Documents are identified by their note slug, not their folder location.
- `EVALUATION.md` — methodology section rewritten to describe title-based qrels as the standard. Explains the TREC qrels convention, normalisation, and why title-based identity is correct for a living vault.
- `OPERATIONS.md` — cron section updated: replace inline `az keyvault secret show` with `source /run/secrets/kairix.env` (populated by `kairix-fetch-secrets.service`). Install instructions updated to `pip install kairix` / `pip install "kairix[neo4j]"`. New Neo4j section: optional dependency, install via `scripts/install-neo4j.sh`.
- `README.md` — install section updated to `pip install`; licence badge updated to Apache 2.0.
- `SECURITY.md` — rewritten to reflect current kairix architecture: tmpfs secrets via systemd oneshot unit, managed identity requirement, Neo4j GPL3 note, Apache 2.0 licence.

## [0.9.0] - 2026-04-14 — Neo4j-native entity system + Docker sidecar secrets

### Added
- **Curator health** (`kairix curator health`) rewritten to query Neo4j exclusively via Cypher. Reports entity counts, synthesis failures, missing vault_paths, and stale entities entirely from the graph — no SQLite dependency. `--no-neo4j` flag removed; client unavailability returns a graceful empty report.
- **entities.db retired**. `kairix/entities/` package deleted in full. Neo4j is the sole canonical entity store. `kairix entity` CLI subcommand removed. All product code (`mcp/server.py`, `briefing/sources.py`, `curator/`) updated to use Neo4j queries only.
- **Docker sidecar secrets via Azure Key Vault.** New `docker/vault-agent/` service: fetches five KV secrets at startup via `DefaultAzureCredential`, writes to tmpfs volume `/run/secrets/kairix.env` (chmod 600), signals readiness via `/run/secrets/.ready`. `kairix` service waits for `vault-agent: service_healthy` before starting.
- **`kairix/secrets.py`** — `load_secrets(path)` reads a `KEY=VALUE` file into env vars without overwriting existing values. Called at module import in `kairix/_azure.py` and `kairix/graph/client.py`. Priority: existing env vars > sidecar secrets > KV subprocess calls.
- **`docker/docker-compose.yml`** — three-service compose: vault-agent, kairix, neo4j:5-community. tmpfs secrets volume (`size=1m, mode=0700`) — secrets never written to disk.
- **`docker/.env.example`** — template for `KAIRIX_KV_NAME`, Azure service principal, path mounts, and Neo4j config.

### Removed
- `kairix/entities/` — entire package (\_\_init\_\_.py, cli.py, schema.py, graph.py, extract.py, pipeline.py, reconcile.py, resolver.py, stop\_entities.py, migrations/001\_initial.sql)
- `tests/entities/` — all entity unit and integration tests
- `KAIRIX_TEST_DB` env var from CI workflows (no longer needed)
- `kairix entity` CLI subcommand

### Changed
- `kairix curator health` now requires a live Neo4j connection; `--no-neo4j` flag no longer accepted
- `kairix/mcp/server.py` `tool_entity()`: entities.db fallback removed; Neo4j miss returns `{"error": "Entity not found: <name>"}` directly
- `kairix/briefing/sources.py` `fetch_recent_decisions()`: entities.db query block removed; decisions sourced from vault only

### Benchmark (v0.9.0, 95 curated queries)
- entity NDCG 0.811 → **0.714** (vault evolution — new content Apr 13–14 shifted gold ranks)
- keyword: 0.616 · procedural: 0.609 · temporal: 0.540 · multi_hop: 0.526 · semantic: 0.501
- **Overall NDCG@10: 0.587** · Hit@5: 0.821 · MRR@10: 0.679

---

## [0.8.1] - 2026-04-13 — Benchmark Infrastructure + Entity Enrichment

### Added
- **`kairix curator health`** — Curator agent health check CLI. Checks for synthesis failures (no summary), missing vault paths, and stale entities (configurable threshold, default 90 days). Reports Neo4j node counts when available. Output: vault-ready Markdown or JSON. Part of the Curator agent.
- **`kairix/llm/`** — `LLMBackend` protocol with `chat()`, `embed()`, `embed_as_bytes()` methods. `AzureOpenAIBackend` and `AnthropicBackend` (stub) implementations. `get_default_backend()` returns `AzureOpenAIBackend`. All product code now receives `LLMBackend` via dependency injection rather than importing backends directly.
- **Repo boundary** — all direct `kairix._azure` imports removed from product code. `hybrid.py` acquires embed via `_get_llm().embed_as_bytes()`. `search/planner.py` acquires chat via `_get_llm().chat()`. No module-level `kairix._azure` imports remain outside `kairix/llm/backends.py`.

### Fixed
- `vector_search_bytes()` now fetches `k × 4` candidates when a date filter is active. `VECTOR_DEFAULT_K=10` was too small for narrow date windows (e.g., "this week") — after force re-embed populated `chunk_date`, the top-10 candidates rarely included docs from a 7-day window, causing vec_count=0 for relative temporal queries.
- All intents now dispatch BM25 + vector in parallel. Previously keyword intent ran BM25-only, causing vector-only docs to miss entirely. Keyword NDCG: 0.48 → **0.62** (+0.110).

### Benchmark (v0.8.1, 95 curated queries)
- keyword NDCG: 0.48 → **0.616** (hybrid fix)
- entity: **0.811** · procedural: 0.609 · temporal: 0.540 · multi_hop: 0.526 · semantic: 0.501
- **Overall NDCG@10: 0.603** · Hit@5: 0.821 · MRR@10: 0.669

## [0.8.0] - 2026-04-11 — CRM Interaction Chunker + Temporal Benchmark Expansion

### Added
- Generic CRM interaction chunker. Processes JSON contact/interaction exports and writes one chunk file per interaction with injected frontmatter (date, contact, meeting_type). Enables CRM timelines to be embedded and searched with temporal filtering. 20 tests.
- Expanded temporal benchmark — 7 new cases (T02–T08) covering absolute date queries (T02–T05) and relative temporal expressions (T06–T08). Demonstrates correct behaviour: absolute date queries bypass date-range filter; relative expressions apply it.

### Notes
- The absolute-vs-relative temporal distinction (introduced in v0.7.0) is now validated with a broader case set.
- CRM interaction chunker is format-agnostic — adapt the provided script to your CRM's export schema.

## [0.7.0] - 2026-04-10 — Temporal Retrieval + Date Infrastructure

### Added
- `chunk_date` column in `content_vectors` — idempotent migration via `schema.py:ensure_vec_table`. Stores the date extracted from each chunk's source document.
- `kairix/embed/date_extract.py` — date extraction at embed time from (1) frontmatter `date`/`created`/`updated`/`created_at` fields (YYYY-MM-DD), (2) YYYY-MM year-month fields (mapped to first of month), (3) filename pattern `YYYY-MM-DD.md`. 24 tests.
- `get_date_filtered_paths(db, start, end)` in `embed/schema.py` — returns `frozenset[str]` of document paths with `chunk_date` in the given window. Used by `hybrid.py` for TEMPORAL intent date-range filtering.
- `is_relative_temporal(query)` in `temporal/rewriter.py` — returns `True` for relative temporal expressions (`last N days/weeks/months`, `recently`, `yesterday`, `today`, `this week/month`). Date filtering is only applied for relative expressions — absolute date references (`March 2026`, `2026-03-09`) query `about` a time period and must not be filtered by chunk_date.
- Date-filtered retrieval in `hybrid.py` — BM25 results post-filtered via `_path_from_file_uri()` + `date_filter_paths`; vector results post-filtered directly on `path`. Both fallback gracefully (no filter applied) when `date_filter_paths` is `None` or empty.
- `scripts/chunk-daily-files.py` — pre-processor for daily log files (`YYYY-MM-DD.md`). Splits on `##` headings, writes section chunks with injected frontmatter so each section inherits its parent document's date. 11 tests.
- `scripts/audit-date-formats.py` — scans vault `.md` frontmatter for date field coverage. Classifies values as ISO / YYYY-MM (year-month) / non-ISO / absent. 13 tests.
- YYYY-MM year-month frontmatter pattern in `date_extract.py` — maps `date: 2025-11` to `2025-11-01`. 6 additional tests.

### Fixed
- `kairix/embed/embed.py` — replaced hardcoded Key Vault name in error messages with `$KAIRIX_KV_NAME` env var reference.

### Benchmark (v0.7.0, 83 curated queries)
- temporal NDCG: 0.369 → **0.382** (date filtering for relative temporal expressions)
- entity: 0.751 · multi_hop: 0.549 · procedural: 0.564 · semantic: 0.519 · keyword: 0.439
- **Overall NDCG@10: 0.5569** · Hit@5: 0.84 · MRR: 0.67

## [0.6.0] - 2026-04-07 — Post-Refactor Benchmark + Relationship Enrichment

### Added
- `scripts/seed-entity-relations.py` — LLM-typed relationship enrichment via GPT-4o-mini batch classifier
- Nightly cron (`0 3 * * * AEST`) — entity extract + relationship seed, Azure KV secret fetch
- `cron-scripts/cron-registry.json` entry for `entity-relation-seed`
- `scripts/build-eval-gold.py` — rebuilds benchmark gold suite from live search + LLM judge
- `suites/v2-real-world.yaml` — fully rebuilt gold suite (263 cases; collection-relative path format)
- Benchmark results: NDCG@10 **0.7756** (entity 0.823, recall 0.788, multi_hop 0.728, temporal 0.810, conceptual 0.804, keyword 0.800, procedural 0.389)
- OPERATIONS.md: comprehensive deployment guide (Azure prerequisites, Key Vault secrets, first-run sequence, cron setup, monitoring, troubleshooting)

### Fixed
- Embed batch retry on dimension mismatch — `ensure_vec_table(db, actual_dims)` called per-batch on dimension error, retries once
- Hourly embed cron: now fetches `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` from Azure Key Vault at runtime (managed identity)
- Gold suite paths: rebuilt to collection-relative format (matching `kairix search` output) after vault refactor broke 196/554 paths

### Benchmark
- NDCG@10 **0.7756** on 263-case suite (vault refactor fully indexed, gold paths rebuilt)
- Entity graph: 1160 entities, 112 typed relationships seeded
- Next milestone: procedural NDCG ≥ 0.55 (current 0.389)

---

## [0.5.3] - 2026-03-28 — 1536-dim Gold Recalibration

### Added
- Recalibrated benchmark instrument after discovering 768-dim baseline was measuring a broken config (extension load order caused silent 0-dim writes)
- Confirmed 1536-dim as correct operational config; rebuilt 252-case gold suite at correct dimensionality
- `scripts/run-benchmark-v2.py`: NDCG@10 scoring engine replacing weighted-total runner

### Benchmark
- 768-dim true baseline: NDCG@10 0.7690 on 252-case suite
- 1536-dim operational: NDCG@10 0.7545 — keyword +0.114, entity +0.043 vs 768-dim

---

## [0.5.2] - 2026-03-26 — Real-World Eval Rebuild

### Added
- Replaced synthetic benchmark with real agent usage queries mined from server logs
- NDCG@10 scoring (was weighted category averages) — 134-case real-world suite
- Temporal routing fix — temporal queries routed to `kairix temporal query` before hybrid search
- Multi-hop pattern improvements — intermediate result reranking, entity bridging
- Suite expanded to 252 cases; multi-category NDCG scoring

### Benchmark
- Initial (instrument issues): NDCG@10 0.3203 on 134-case suite
- After instrument + temporal fix: NDCG@10 improved to 0.69+ range before recalibration

---

## [0.5.1] - 2026-04-06 — Entity Graph + Multi-Hop Planner

### Added
- Multi-hop QueryPlanner — GPT-4o-mini decomposes complex queries into sub-queries, parallel BM25+vector dispatch, result synthesis
- Entity graph seeded from vault-entities collection; entity boost wired into planner context injection
- `kairix entity extract --changed` incremental extraction pipeline
- `scripts/seed-entity-relations.py` (pattern-matching v1 — superseded by LLM classifier)

### Benchmark
- NDCG@10 0.7541 on 245-case suite — multi_hop 0.716 (+0.035 vs prior), entity 0.677

---

## [0.5.0] - 2026-03-23 — Temporal + Summaries + Wikilinks

### Added
- Temporal chunker + query rewriter + timeline CLI
- L0/L1 summaries generation (gpt-4o-mini) + tier router
- Wikilink injector + entity resolver + audit CLI
- Entity NER extraction pipeline + ontology reconciler
- Raw query logging: `KAIRIX_LOG_QUERIES=1` → queries.jsonl
- `scripts/analyze_queries.py`: real query distribution analysis
- Keyword zero-result fallback to vector search

### Fixed
- Vector index re-embedded at 1536-dim (was 768-dim — vectors never landed in vectors_vec)
- KV cold-start causing entity vector search failures (20-45% failure rate)
- Keyword queries returning 0 results when BM25 returns empty

## [0.4.0] - 2026-03-23 — Briefing + Classification

### Added
- `kairix brief <agent>` — 8-step concurrent briefing pipeline synthesises ~800-token session context from memory logs, entity stubs, rules, decisions, and hybrid search via GPT-4o-mini
- `kairix classify "<content>"` — two-stage auto-classification (rule-based first, LLM fallback) routes new writes to the correct vault file with confidence score
- `kairix/_azure.py`: `chat_completion()` for GPT-4o-mini synthesis calls
- `kairix/briefing/`: pipeline.py, sources.py, synthesiser.py, writer.py, cli.py — 48 tests
- `kairix/classify/`: rules.py, judge.py, router.py, cli.py — 83 tests
- Benchmark suite v1.1: CL01–CL04 classification cases; classification scoring in runner
- ENGINEERING.md: entity failure-mode patterns, benchmark suite maintenance rules, gold-path validity rules

### Fixed
- LLM judge KV secret name: `azure-openai-gpt4o-mini-deployment` (was `azure-openai-deployment` — silent 0.0 scoring on all LLM-judged benchmark cases)
- RRF path dedup: `_canonical_path()` strips collection prefix so BM25 and vector results for entity stubs now merge correctly in fused dict
- Entity benchmark gold paths: E01–E06 now have `gold_path` + `score_method: exact` (was `null`/`llm` — LLM judge had no ground truth, scored 0.2–0.4 on tangential docs)
- Entity stub content: jordan-blake.md, acme-corp.md, platform.md enriched to 650–750 words; project-x.md to 490 words

### Benchmark
- entity: 0.300 → 0.933 (gold-path fix + stub enrichment)
- classification: 1.000 (4/4 rule-based, deterministic)
- recall: 0.875 (stable)

---

## [0.3.0] - 2026-03-23 — Entity Benchmark Repair

### Added
- Entity stub enrichment: jordan-blake.md, acme-corp.md, platform.md, project-x.md enriched to ≥500 words
- Gold paths added to entity benchmark cases E01–E06

### Fixed
- Entity score collapse (0.733→0.300): root cause — benchmark gold_path: null + sparse stub content

## [0.2.0] - 2026-03-22

### Added
- Intent classifier (keyword/semantic/temporal/entity/procedural)
- BM25 wrapper (subprocess → structured results)
- Vector search wrapper (sqlite-vec CTE MATCH)
- RRF fusion + entity boost
- Token budget enforcer (L0/L1/L2 tiers)
- Hybrid orchestrator + parallel dispatch
- `kairix search` CLI
- Entity graph schema + migration system
- Entity graph (write, lookup, mentions, relationships)
- `kairix entity` CLI
- Benchmark CLI: YAML suite format, validate/run/compare/init commands
- Generalised benchmark framework SPEC.md
- CI: 4-stage pipeline, mypy strict, ruff, bandit, pip-audit, Dependabot
- ENGINEERING.md contributor guide

### Fixed
- sqlite-vec CTE pattern: MATCH must be primary table in inner CTE
- Collection scope: _SHARED_COLLECTIONS was missing vault (93% of content)
- Benchmark gold-pair validity: several benchmark gold pairs replaced with valid pairs

## [0.1.0] - 2026-03-22

### Added
- Azure OpenAI embedding pipeline (text-embedding-3-large, 1536-dim)
- Schema validation + sqlite-vec extension loading
- Staging table pattern for vec0 upserts
- Recall gate (5/5 known-doc queries post-embed)
- `kairix embed` CLI
- 50-query benchmark runner (BM25 baseline: 0.5054)
