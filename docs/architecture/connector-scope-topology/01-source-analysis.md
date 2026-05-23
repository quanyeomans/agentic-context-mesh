# Source analysis — per-connector model

Per-connector breakdown along nine dimensions: auth model, scope hierarchy,
change detection, content shape, sensitivity model, freshness expectation,
storage characteristics, constraints / gotchas, open questions.

Constellation:

- **Shipped (Wave 2 + 5)**: obsidian, dex_crm, m365_email_headers, m365_calendar
- **Planned (research first)**: sharepoint, onedrive, notion, teams, slack, github, google_drive
- **Likely**: local-filesystem (non-obsidian), email-imap, confluence, jira

Each section is self-contained so an operator landing on it can decide
the connector's instance / collection shape without reading the rest.

---

## Shipped connectors

### Obsidian (`kairix.connectors.obsidian`)

**Auth model**. None — local filesystem read. Operator-provided
`vault_root` path is the only "credential". Each vault is a separate
read scope; vault keys (Obsidian's per-vault encryption) are
out-of-scope (kairix sees the decrypted .md files on disk).

**Scope hierarchy**. Per-instance: one vault. Inside one vault:
folder tree → `.md` files → (optionally) frontmatter. The connector's
internal `CollectionConfig` list lets you scope by sub-folder (name +
path + glob + exclude), but all output chunks today land in one SQL
collection equal to the connector entry's `name`.

**Change detection**. `watchdog` filesystem observer (real-time
events) + a `FullScanReconciler` on first run and every N (default 10)
calls. Cursor is an ISO-8601 timestamp; events with `modified_at <=
cursor` are filtered. Cold-start reconciler walks the full vault and
emits one `created` event per discovered file; subsequent calls drain
the watchdog queue and re-reconcile every Nth tick.

**Content shape**. `.md` text. Conversion is passthrough (raw markdown
text) or `markitdown` for richer extraction (frontmatter, lists,
tables, code fences). Per-page identity uses the file path; per-chunk
identity uses `obsidian://open?vault=<vault>&file=<path>#<chunk_index>`
deep links so the source_uri navigates back to the live note.

**Sensitivity**. Per-connector default (config `sensitivity` field).
Today no per-folder override surface. F39 demands every chunk carry
`sensitivity`; the default falls back to `internal` if unspecified.

**Freshness**. Realtime via watchdog (sub-second for already-running
worker). Cold-start full-walk takes O(file_count × hash_cost) — ~5,700
files in the live dogfood vault completed in seconds.

**Storage**. ~12 chunks per file in current Silver chunker. 5,754
files → 68,814 chunks → ~150MB of SQLite (rough — vectors dominate).
Bronze raw-blob retention is one record per `(item_id)` regardless of
chunk count, so bronze is small.

**Constraints / gotchas**.
- **Vault key is per-vault**: cannot share one connector instance
  across two encrypted vaults.
- **Path-rename semantics**: a vault rename surfaces as `deleted` +
  `created` events — orphan chunks linger until the cursor + reconciler
  catches up.
- **Watchdog on network mounts** is unreliable (SMB / NFS event
  delivery). Reconciler-every-N is the safety net.
- **Frontmatter sensitivity hint**: not yet wired — operator wants
  `tier: confidential` in frontmatter to set per-file sensitivity, but
  currently sensitivity is connector-level.

**Open questions**. (1) Per-folder collection routing — should one
obsidian connector emit chunks to multiple kairix collections based on
folder path? (2) Frontmatter-driven per-file sensitivity. (3) Symlink
handling. (4) Attachment handling (PDF / images inside a vault) —
today extractor=passthrough drops them; should they route to the PDF
extractor?

---

### Dex CRM (`kairix.connectors.dex_crm`)

**Auth model**. Bearer token (API key) loaded from
`kairix.secrets.get_secret("connector-dex-api-key")` — KV / sidecar /
file secret store. One key covers ONE Dex tenant (the user's CRM
workspace). No multi-tenant under one credential.

**Scope hierarchy**. Per-instance: one Dex tenant. Inside: three
record kinds — `contacts` (Person), `organisations` (Org),
`relationships` (Person-Org edges). No further hierarchy; flat record
streams.

**Change detection**. `updated_after` query parameter on each listing
endpoint. Cursor = ISO-8601 timestamp of the latest record processed.
Connector rate-limits to ~1 req/sec with exponential back-off on HTTP
429.

**Content shape**. JSON records. Silver lifts them into
`EntitySignal` rows staged in SQLite `entity_signals`; a separate
Curator worker pushes the signals to Neo4j (ADR-018 — staging keeps
the connector path synchronous + cheap). No chunk text — Dex records
ARE the entity payload, not a document to chunk.

**Sensitivity**. Per-connector default (`sensitivity` config); Dex
records are uniformly the operator's-CRM scope, no per-record tier
distinction (everything is `internal` or `confidential` depending on
operator's posture).

**Freshness**. Polling cadence operator-set; default 1-hour at worker
maintenance cycle. Updates within Dex propagate to kairix within one
poll. No webhook surface in Dex's API (or none surfaced through the
docs we've inspected).

**Storage**. Small — typical Dex tenant ≤ 10k contacts, ≤ 2k orgs,
≤ 50k relationships. EntitySignal rows are compact (~150 bytes
each). Whole-corpus ingest of a year-old Dex tenant runs in single-digit
minutes.

**Constraints / gotchas**.
- **No deletion delta** — Dex doesn't surface deleted records in the
  `updated_after` feed. Tombstone reconciliation requires periodic
  full re-list (not yet implemented).
- **Rate-limit envelope undocumented** by Dex publicly; the
  conservative 1-req/sec is operator-friendly but adds latency on
  initial ingest.
- **Single-tenant per key** — multi-tenant operators need one
  connector instance per tenant, hence the multi-instance design
  motivation.

**Open questions**. (1) Does Dex have a webhook surface for
realtime push? (2) Multi-tenant operators — how many simultaneous Dex
instances do we expect? (3) Reconcile-deletes via full-list periodically
— what cadence is operator-acceptable?

---

### M365 email headers (`kairix.connectors.m365_email_headers`)

**Auth model**. OAuth2 client-credentials (app-only) with admin
consent for `Mail.Read` (or `Mail.ReadBasic.All` for header-only).
Per ADR-004, **bodies never ingested** — header-only.
`connector-m365-{tenant-id,client-id,client-secret}` secret triple.
One credential covers ONE M365 tenant. Within the tenant the app-only
principal can read ALL mailboxes (admin-consented) or specific user
mailboxes (`Mail.Read.Shared`).

**Scope hierarchy**. Tenant → mailbox (user) → folders → messages.
For ingestion we typically iterate `/users/{id}/messages` for selected
users; folder-level scoping is available but rarely needed for
header-only.

**Change detection**. Microsoft Graph delta query
(`/users/{id}/mailFolders/{id}/messages/delta`). Per-mailbox delta
token. Webhooks via Graph change-notifications also available for
near-realtime.

**Content shape**. Header fields only: From / To / Cc / Subject /
Sent / Received / Conversation-ID. Encoded as a compact text payload
for Silver → chunking is trivial (one chunk per header bundle).
Bodies never read; F15 secret-leak surface stays narrow.

**Sensitivity**. Header-only data is inherently lower-sensitivity
than body content (no claim text, no PII bodies). Default
`confidential` because subject lines may carry sensitive content.

**Freshness**. Sub-minute via webhook or 5–15min via delta polling
depending on operator preference.

**Storage**. Small per message (~500 bytes encoded headers). A busy
tenant might generate 100k messages/month → 50MB/month of header
data. Vector cost dominant.

**Constraints / gotchas**.
- **Multi-mailbox per credential** — one connector instance can cover
  many users in the same tenant.
- **App-only requires admin consent** — operators on shared M365 must
  loop their tenant admin in. Smaller operators may prefer delegated
  flow (one user's mailbox via that user's OAuth).
- **Conversation threading** — Graph emits messages flat; thread
  reconstruction lives downstream (Silver / search).
- **No body content** is by design (ADR-004) — if an operator wants
  body content there's a separate, sensitivity-tagged connector that
  must be explicitly opted in.

**Open questions**. (1) Should body-content be a separate connector
plugin (`m365_email_bodies`) gated by a flag, or a config-flag on the
headers connector? (2) Per-user scoping vs whole-tenant scoping —
default operator pattern?

---

### M365 calendar (`kairix.connectors.m365_calendar`)

**Auth model**. Same OAuth2 client-credentials as m365_email_headers
(`Calendars.Read.All` scope). Same credential triple. Same tenant
boundary.

**Scope hierarchy**. Tenant → user → calendar(s) → event. Most users
have one primary calendar; shared room / resource calendars exist
separately.

**Change detection**. Graph delta query
(`/users/{id}/calendarView/delta`). Per-calendar delta token. Webhook
notifications available.

**Content shape**. Event metadata: Subject / Start / End / Location /
Attendees / Organiser / Body (HTML / text). Body inclusion is a
sensitivity call — kairix's plugin opts in to body for events because
event bodies are typically agenda-like (less PII risk than email).

**Sensitivity**. Default `internal`. Per-event sensitivity field on
Outlook events (Normal / Personal / Private / Confidential) MAY be
surfaceable but not yet wired.

**Freshness**. Sub-minute via webhook or 15min polling.

**Storage**. Smaller than mail — typical user ≤ 50 events/week. A
tenant with 100 users → ~5k events/week → manageable.

**Constraints / gotchas**.
- **Recurring events**: Graph returns the series master + occurrence
  exceptions; calendarView expands occurrences. Connector ingests
  expanded form so each occurrence is a distinct chunk.
- **All-day events** have timezone subtleties; the connector stores
  the UTC start/end and the original timezone hint.

**Open questions**. (1) Should we wire the Outlook
`event.sensitivity` field through to F39? (2) Room / resource
calendars — opt-in?

---

## Planned connectors (research-driven; see in-flight subagents)

### SharePoint + OneDrive (Microsoft Graph)

**Auth model**. Three credential shapes:
- **Delegated user OAuth** (auth code + PKCE / device code) — `Files.Read.All`, `Sites.Read.All`, `offline_access`. App acts as a signed-in user; sees only what that user can. Refresh tokens 90 days idle. Operator-friendly for "ingest what I can see" — no admin loop beyond app registration.
- **App-only / client credentials, broad** — `Files.Read.All` + `Sites.Read.All` (application). Tenant-admin consent required. Sees every drive in tenant. For "whole-tenant compliance".
- **App-only with `Sites.Selected`** — sees no sites until a site admin POSTs `/sites/{id}/permissions` grant. Per-site allowlist. Right shape when scoping must be a known site set without tenant-wide read.

For "pull all docs user X can see": delegated. For tenant-wide: app-only broad. `Sites.Selected` sits between when legal posture demands per-site approval.

**Scope hierarchy**. `Tenant → Site → Drive (document library) → DriveItem`. OneDrive personal = `/users/{id}/drive`. SharePoint shared drives via `/sites/{id}/drives`. **Teams channel files** live in the parent team's SharePoint site under `/teams/{id}/channels/{id}/filesFolder` — channel files are SharePoint DriveItems under the hood. **Auth-relevant**: tenant (one credential = one tenant; no cross-tenant) and the per-site `Sites.Selected` fence. **Logical-only**: site / drive / folder — one credential enumerates across them.

**Change detection**. `GET /drives/{drive-id}/root/delta` returns changed items + `@odata.deltaLink`. **Token is per-drive** — no tenant-wide change feed in v1.0; tenant-wide ingest = N delta loops, one per drive. Permission-only changes still produce delta entries (item's `permissions` facet reflects new state). Deleted items have a `deleted` facet. Renames / moves are updates to `parentReference`.

**Content shape**. DriveItem variants: `folder`, `file`, `package` (OneNote, Loop — opaque). MIME types: docx / xlsx / pptx / pdf / msg / md / txt / images. Graph returns **bytes** via 302 to pre-authenticated download URL (expires minutes; stream don't buffer). No server-side text extraction — kairix's Wave 4 extractors (markitdown / pdfplumber / python-docx / openpyxl / python-pptx) cover this. Server-side `/content?format=pdf` available to convert office docs first.

**Sensitivity**. Purview/MIP labels via `DriveItem.sensitivityLabel` + `POST /drives/{id}/items/{id}/extractSensitivityLabels`. Label list per site via `/sites/{id}/informationProtection/sensitivityLabels`. **Mapping to F39 is operator-policy** — config map `{label_guid: kairix_tier}` per tenant. Default unlabelled → `internal` in corporate tenant; `public` only on explicit opt-in.

**Permissions model**. `GET /drives/{id}/items/{id}/permissions` returns full ACL: grantedTo (user) + grantedToIdentities (multi-user) + sharing-link metadata. Answers BOTH "who can access" AND "can principal X access" — provided the credential has read on the parent site. Under delegated we see only what the user sees; under app-only we see full tenant ACL graph.

**Freshness + throttling**. Operator delta cadence: 1–5 min active drives, 15–60 min cold. Sub-minute pulls trip throttling. **Webhooks** (`/subscriptions`) cover `driveItem` for OneDrive + SharePoint, notification-only (no content) — wake the delta loop, don't replace it. Subscription lifetime ~3 days; needs renewal. Throttling envelope ~10k req/10min per app per tenant (operator-observed, not contractual); `Retry-After` on 429/503.

**Storage / volume**. Small-team: 1–5 sites, 10k–100k items. Mid-org: 100–1000 sites, 1M–10M items. Enterprise: 10k+ sites, 100M+ items. Per-file extract CPU cost: docx ~50–200ms, xlsx ~100ms–2s (workbook size), pptx ~200–800ms, pdf ~100ms–5s (OCR explodes this).

**Gotchas**.
- `Sites.Selected` requires per-site grant by site admin — no tenant-one-shot.
- Recycle-bin items don't appear in delta; permanent deletes do (as `deleted` facets).
- Large-file download via 302 — stream, don't buffer; URL expires.
- Retention-held items still visible in delta even when user-deleted.
- Broad first-time enumeration of huge tenant throttles hard — initial delta per-drive with backoff, not fan-out.
- Teams private / shared channels live on separate SharePoint sites — enumerate `/teams/{id}/channels` and resolve `filesFolder` per channel.
- `package`-type items (OneNote, Loop) opaque to drive-content download.
- Delta tokens can invalidate server-side — handle `resync required` by restarting `/delta` without token.

**Sources**: [DriveItem](https://learn.microsoft.com/en-us/graph/api/resources/driveitem), [Delta query](https://learn.microsoft.com/en-us/graph/api/driveitem-delta), [List permissions](https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions), [Sites.Selected](https://learn.microsoft.com/en-us/graph/permissions-reference#sitesselected), [Sensitivity labels](https://learn.microsoft.com/en-us/graph/api/resources/sensitivitylabel), [Webhooks](https://learn.microsoft.com/en-us/graph/webhooks), [Throttling](https://learn.microsoft.com/en-us/graph/throttling), [Teams channel files](https://learn.microsoft.com/en-us/graph/api/channel-get-filesfolder).

---

### Notion

**Auth model**. Two shapes (`Notion-Version: 2022-06-28` stable):
- **Internal integration tokens** — long-lived `secret_...` tokens created in Settings → Integrations. Workspace-scoped: one token = one workspace. Best fit for engagement-scope mirrors.
- **Public OAuth integrations** — three-legged OAuth; access token belongs to the integration's bot user (not the installing user). Also workspace-scoped — installation produces one token per workspace.

**No single credential reaches multiple workspaces** — multi-workspace coverage = N tokens.

**Scope hierarchy**. `workspace → teamspace → page / database → block`. Databases are a page type; rows are pages. **An integration installed into a workspace does NOT automatically see every page** — it sees only pages explicitly shared with it via the "Connections" menu, or pages inheriting access from a shared ancestor. Access is page-subtree-by-page-subtree, not workspace-wide. The `POST /v1/search` endpoint enumerates the visible surface.

**Change detection**. Historically polling-only: `POST /v1/search` and `POST /v1/databases/{id}/query` both accept `sort` by `last_edited_time` descending — track high-water-mark, query for newer. Notion's **Webhooks/Subscriptions API** (beta / GA rolling 2024–2025) pushes `page.created`/`updated`/`deleted` and `database.updated`. Treat webhooks as realtime, polling as reconcile fallback. **Rate limit ~3 req/s per integration** smoothed (bursts allowed but sustained excess returns 429 + `Retry-After`). No published daily quota.

**Content shape**. `GET /v1/pages/{id}` returns metadata + properties (title, select, date, people, relation, formula, rollup — rich-typed). Body content via `GET /v1/blocks/{id}/children` + recurse into `has_children: true`. Block types: `paragraph`, `heading_1..3`, `bulleted_list_item`, `numbered_list_item`, `to_do`, `toggle`, `code`, `quote`, `callout`, `table`, `image`, `file`, `bookmark`, `child_page`, `child_database`, `synced_block`, `column_list`/`column`. Each block has `rich_text` with annotations (bold, italic, link, mention).

Ingest conversion: recursive block-tree walk → flatten to Markdown (headings as `#`/`##`/`###`, lists as `-`/`1.`, code fences from `code`, callouts as block-quotes). Page **properties** map to kairix document metadata. **Databases** ingest as page-collections: each row is a page via `databases/{id}/query`; database schema becomes a metadata template applied to row-pages.

**Sensitivity**. No first-class "this page is confidential" flag. Indirect signals: `public_url` (non-null → published web), `parent` (workspace-root vs page-child vs teamspace), the integration's visible-page set. **Mapping to F39 is operator-declared** in connector config (e.g. `teamspace:Engineering → internal`, `teamspace:Legal → confidential`, `public_url != null → public`).

**Freshness**. With webhooks: sub-minute. Polling: 5–15 min cadence common (bounded by 3 req/s ceiling — re-scanning 10k pages via search + per-page block-fetches takes minutes of API time).

**Storage / volume**. Small team (5–20 people): hundreds to low-thousands of pages, 200–2000 words each. Large org: 50k–500k+ pages. **Databases skew DENSE** (many small row-pages — task trackers / CRMs / OKRs with 10–500 chars body); doc-style page trees skew sparse. Plan ingest cost on row-count, not page-count.

**Gotchas**.
- **Archived vs deleted**: `archived: true` flag (recoverable, still returned with `archived` filter); hard deletion 404s. `last_edited_time` updates on archive; hard deletes do NOT appear in search — reconcile-sweep diff required.
- **Page moves**: `parent` changes, `id` is stable. Don't key mirror on path.
- **Rate-limit bursts**: 429 + `Retry-After`; concurrent workers share a token bucket.
- **Permission revocation**: removing integration's page connection → subsequent reads 404 / `object_not_found`. Distinguish from "deleted" — surface as "no longer accessible".
- **Block-tree depth**: synced blocks + column layouts can recurse surprisingly; cap depth to bound rate-limit consumption.
- **Rich-text fidelity loss**: Markdown drops color, underline, certain mentions. Acceptable for retrieval, lossy for round-trip.

**Sources**: [Auth](https://developers.notion.com/docs/authorization), [Versioning](https://developers.notion.com/reference/versioning), [Search](https://developers.notion.com/reference/post-search), [Block children](https://developers.notion.com/reference/get-block-children), [Block types](https://developers.notion.com/reference/block), [Rate limits](https://developers.notion.com/reference/request-limits), [Webhooks](https://developers.notion.com/reference/webhooks).

### Teams (Microsoft Graph)

Same tenant credential family as M365 mail / calendar — app-only OAuth or delegated. Scope hierarchy: tenant → team → channel → message + attached files. Teams **channel files** live on the team's SharePoint site (see SharePoint section) — so a Teams connector REUSES SharePoint's delta surface for the file path and adds `/teams/{id}/channels/{id}/messages/delta` for the chat surface.

Sensitivity inherits from team / channel privacy: public team + public channel → `internal`; private channel → `confidential`; private team → `confidential` (or `restricted` per operator policy).

Open questions: do we ingest reactions / Loop components / meeting transcripts as separate kinds? (Likely deferred to a follow-up phase; chat + files is the MVP.)

---

### Slack

**Auth model**. Three shapes: bot tokens (`xoxb-…`), user tokens (`xoxp-…`), app-level tokens (`xapp-…`) for Socket Mode. Bot tokens are bound to a single installed workspace by default. Multi-workspace via OAuth v2 — each install yields its own token pair. Enterprise Grid orgs can issue an org-wide bot token via `admin.*` scopes. **One credential = one workspace** (or one Grid org); no token spans unrelated workspaces.

**Scope hierarchy**. Workspace → channel → thread → message → file/attachment. Read scopes split per channel type: `channels:history` (public), `groups:history` (private), `im:history` (DMs), `mpim:history` (group DMs); plus `files:read`. Bot tokens only see channels they're invited to. **Auth-defined**: workspace + channel-membership. **Logical**: thread / message.

**Change detection**. Three paths: (1) Events API HTTP webhooks; (2) Socket Mode WebSocket; (3) delta polling via `conversations.history` with `oldest=<cursor_ts>`. Rate limits tier-based: `conversations.history` Tier 3 (~50 req/min per method per workspace). Events API retry envelope: 3 retries, 1s/1m/5m.

**Content shape**. Messages JSON with `text` (mrkdwn), `blocks` (Block Kit JSON — needs flattening), `attachments` (legacy), `files` (downloaded via `files.info` + bearer-auth private URL). File types: images, PDFs, snippets, Slack-native posts/canvases (canvases via `canvases.*` — non-trivial markdown conversion). Threads via `conversations.replies` keyed on `thread_ts`.

**Sensitivity** (F39 mapping). Public channel → `internal`. Private channel / MPIM → `confidential`. DM → `restricted`. Files inherit most-permissive channel.

**Freshness**. Events API: seconds. Polling: 1–5 min per active channel within Tier 3 budget. Backfill of large channels = cursor-paginated 200 msgs/page.

**Storage / volume**. Active workspaces 10k–1M messages/year; file attachments dominate storage. Conversion: cheap for text/blocks, moderate for files (HTTP + bearer-auth).

**Gotchas**.
- Message edits arrive as `message_changed` subtype with embedded `previous_message` — naive consumers double-count.
- Deletions arrive as `message_deleted` — only the `ts` remains.
- **Free-tier workspaces hide messages >90 days from the API** (2024 policy).
- Archived channels readable but no events emitted.
- File URLs are private + auth-header download; signed URLs expire.

**Sources**: [Web API](https://api.slack.com/web), [Scopes](https://api.slack.com/scopes), [Rate limits](https://api.slack.com/apis/rate-limits), [Events API](https://api.slack.com/apis/events-api), [Socket Mode](https://api.slack.com/apis/socket-mode).

---

### GitHub

**Auth model**. Four shapes: (1) **PAT classic** (`repo`, `read:org` — broad, user-bound); (2) **Fine-grained PAT** (per-repo, per-permission, expires); (3) **GitHub App installation token** (bound to one installation — one org or selected repos; exchanged from JWT signed by App's private key; expires 1h); (4) OAuth App user tokens. **One App can have many installations**, each independent. PATs span multiple orgs the user belongs to per scope; App tokens strictly scoped to installation.

**Scope hierarchy**. Org → repo → branch / ref → tree → file (blob). Plus per-repo: issues, PRs, discussions, releases, wiki, projects, actions, packages. **Auth-defined**: org membership, repo visibility, branch protection. **Logical**: tree path, file path. Fine-grained PATs and Apps subdivide: `contents:read`, `issues:read`, `pull_requests:read`, `metadata:read`, `discussions:read`.

**Change detection**. Webhooks (org- or repo-level): `push`, `issues`, `pull_request`, `discussion`, `release`, `repository`, `delete`. App installations also receive `installation_repositories`. GraphQL `repository.refs` + tree diffs. Rate limits: PAT = 5000 req/h REST + 5000 points/h GraphQL; App = 5000–15000/h scaling with installation seat count. Secondary rate limits on concurrent / abuse patterns (403 + `Retry-After`).

**Content shape**. Source files via `GET /repos/{o}/{r}/contents/{path}` (base64, ≤1 MB) or Git Trees API for larger. Issues/PRs/discussions = JSON with markdown bodies + comment threads. Wikis = separate Git repo. Releases = markdown notes + binary assets.

**Sensitivity** (F39 mapping). Public repo → `public`. Internal repo (GHEC orgs) → `internal`. Private repo → `confidential` (or `restricted` if it carries secrets / customer data — flag at org policy level, not derivable from API).

**Freshness**. Webhooks: seconds. Polling realistic at 1 min per active repo within rate budget. Org-wide diff via `GET /orgs/{org}/events` (limited to 300 events / 90 days).

**Storage / volume**. Per repo: 10²–10⁵ files; issues/PRs 10²–10⁴; monorepos 10⁶ files. Tree-walking via Git Trees `recursive=1` truncates at 100k entries — fall back to clone for larger.

**Gotchas**.
- Archived repos = read-only + no webhooks. Must poll or accept staleness.
- Force-pushes rewrite history; ingest pipelines keyed on commit SHA must reconcile.
- Large files (>100 MB) need LFS API (separate auth).
- Secondary rate limits punish bursty parallelism — sequential per-installation safer.
- PAT classic scopes coarse (`repo` = all-or-nothing).

**Sources**: [REST](https://docs.github.com/en/rest), [GraphQL](https://docs.github.com/en/graphql), [App auth](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app), [Rate limits](https://docs.github.com/en/rest/rate-limit), [Webhooks](https://docs.github.com/en/webhooks).

---

### Google Drive

**Auth model**. Two shapes: (1) **OAuth 2.0 user credentials** — one refresh token = one user's Drive view; (2) **Service Account with domain-wide delegation** (Workspace only) — admin grants the SA the right to impersonate any user in the domain; one SA key reaches every user's Drive in the workspace. SA without DWD sees only files explicitly shared to the SA's email + Shared Drives it's added to.

**Scope hierarchy**. Per credential: My Drive (owned), Shared Drives (member), Shared-with-me (received). **Auth-defined**: Workspace domain membership, Shared Drive membership, per-file ACL. **Logical**: folder hierarchy (folders = files with MIME `application/vnd.google-apps.folder`; single-parent model post-2020). OAuth scopes: `drive.readonly` (everything user can see), `drive.metadata.readonly`, `drive.file` (only files the app created/opened — too narrow for ingest).

**Change detection**. (1) `changes.list` with `pageToken` from `changes.getStartPageToken` — delta polling per user or per Shared Drive; (2) `files.watch` / `changes.watch` push notifications to HTTPS webhook (channels expire ≤7 days, must renew). Rate limits: 1,000 queries / 100 sec / user; 10,000 / 100 sec / project (raise via quota request).

**Content shape**. Native Google types need export: Docs → `text/plain` or `text/markdown` (markdown export GA 2024) via `files.export`; Sheets → `text/csv` (per-sheet); Slides → `text/plain` or `application/pdf`. Binary files (PDF, DOCX, images) via `files.get?alt=media`. **Export cap: 10 MB**; larger files via the recent `files.download` endpoint. Comments + suggestions via `comments.list`.

**Sensitivity** (F39 mapping). Drive visibility tiers: `private` / `restricted` (named ACL) / `domain` (anyone in Workspace with link) / `anyone-with-link` / `public`. Mapping: `public`/`anyone-with-link` → `public`; `domain` → `internal`; `restricted`/`private` → `confidential` (or `restricted` for labelled-sensitive Shared Drives via Workspace Labels API).

**Freshness**. Push notifications: seconds, but channels need re-subscription every 7 days. Polling: 1–5 min per Shared Drive via `changes.list`. My-Drive enumeration across a domain via DWD is the slow path — hours for initial backfill.

**Storage / volume**. Per-user Drives: 10³–10⁵ files; corporate Shared Drives 10⁶+. Conversion cost: high for Slides/PDFs (OCR for scanned PDFs); moderate for Docs (markdown export is now cheap); low for plain text.

**Gotchas**.
- Trashed files still appear in `changes.list` with `removed=false` + `trashed=true` — must filter.
- Files removed from a Shared Drive emit `removed=true` but may still exist in another drive (multi-homing edge case).
- Export endpoint silently truncates >10 MB exports — check `Content-Length`.
- DWD requires admin consent and is auditable — never enable without explicit org policy approval.
- `shared-with-me` is a view, not a container — files owned elsewhere; ACLs can change without notice.

**Sources**: [v3 reference](https://developers.google.com/drive/api/reference/rest/v3), [Auth scopes](https://developers.google.com/drive/api/guides/about-auth), [Changes/watch](https://developers.google.com/drive/api/guides/manage-changes), [Export formats](https://developers.google.com/drive/api/guides/ref-export-formats), [DWD](https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority).

---

## Cross-system synthesis

---

Five patterns the design must accommodate, drawn from the per-source analyses:

1. **Credential boundaries differ from container boundaries.** SharePoint, Notion, M365, Slack, GitHub, Google Drive: one credential covers many internal containers (sites/workspaces/teams/repos/drives). Obsidian: one credential per logical store. The connector framework's `name` field has been collapsing these — needs to split into (credential-boundary identity) and (container-discovery / cursor scope).

2. **Cursors are per-container, not per-credential.** Microsoft Graph delta is per-drive (SharePoint/OneDrive), per-mailbox (mail), per-calendar. Google Drive `changes.list` is per-user or per-Shared-Drive. Slack is per-channel. Notion is per-page-tree (effectively per-search-query high-water-mark). The current single-`cursor_token` per `(connector_name)` in `connector_cursors` is too narrow — schema needs `(connector_name, container_id)`.

3. **Sensitivity is per-item-with-source-default.** SharePoint Purview labels, Slack channel privacy, GitHub repo visibility, Google Drive sharing tiers each carry per-item signal that should override connector defaults. Notion is the exception — no first-class flag; mapping must be operator-policy. Design should accept BOTH per-item runtime sensitivity (when source provides) AND operator-config mapping fallback.

4. **Webhook + reconciler is the universal pattern.** Every source has a push path (events / webhooks / sockets) for sub-minute freshness AND requires a periodic reconcile poll because push is best-effort (subscription expiry, missed events, archive semantics). The connector framework already encodes this for Obsidian (watchdog + FullScanReconciler every Nth call); the same pattern repeats everywhere. Should be a Protocol-level affordance, not per-connector reinvention.

5. **Deletion semantics are inconsistent.** Notion archives recoverably + hard-deletes 404 (reconcile-sweep needed). Slack message-deletes lose payload. Dex has no deletion feed. GitHub force-pushes rewrite history. Google Drive trashed files require filtering. SharePoint delta surfaces deletes with a `deleted` facet. The connector Protocol's tombstone surface needs to be richer than "deleted=True/False" — needs to distinguish (deleted-from-source / archived-recoverable / no-longer-accessible-to-credential).

These observations directly inform the §02 use-case design, §03 BDD scenarios, and ultimately the ADR shape.

---

## Open research questions tracked

Per-connector open questions are inline above; the consolidated list
(to drive future operator-confirmation rounds + external doc dives):

- Dex CRM: webhook availability; multi-tenant operator patterns.
- M365 mail: body-content connector — separate plugin or config flag?
- M365 calendar: surface `event.sensitivity` through F39?
- Obsidian: frontmatter-driven per-file sensitivity?
- SharePoint: sensitivity-label retrieval via Graph?
- Notion: integration-token reach across workspaces — confirm.
- Teams: per-channel privacy → F39 mapping shape.
- Slack: edit-history retention behaviour.
- GitHub: PAT vs GitHub App scope semantics for org-wide vs per-repo.
- Google Drive: shared-with-me visibility under app-only.
