# Research closeout — five open questions answered

Closes the five "Open questions tracked" items from `01-source-analysis.md`. Each section is the answer + citation + the design decision the answer drives.

---

## 1. Dex CRM webhook availability

**Finding: Dex is polling-only. No webhook / push-notification surface in the public API.**

The Dex User API at [getdex.com/docs/api-reference](https://getdex.com/docs/api-reference) (mirrored in the [Postman collection](https://documenter.getpostman.com/view/28038282/2s9XxsUbPx)) exposes Contacts, Notes, Reminders, Tags, Groups, Search — every entry is REST CRUD. No `/webhooks`, `/subscriptions`, `/events`, or callback registration. No SSE / long-poll stream documented. Auth is static `Authorization: Bearer <api_key>` — no signing secret / HMAC field that would exist if outbound webhooks shipped. Cross-confirmed via Zapier: [Dex's Zapier app](https://zapier.com/apps/dex/integrations) exposes only Actions + Search Actions; **zero triggers** (instant-trigger absence in Zapier is the standard tell for "no webhooks").

**Design decision**: `kairix.connectors.dex` is poll-only. Change detection via `updated_at` bookmark; default 5-min cadence (operator-tunable up to 1h for low-volume tenants). No `WatchableConnector` Protocol surface implementation. F37 unaffected (no streaming-library imports needed). Staleness window = poll cadence — acceptable for personal-CRM use case.

---

## 2. M365 email-body connector shape — separate plugin or config flag?

**Finding: Onyx ships a single combined Gmail connector with no body/headers split. Recommend (b) — config flag on the existing `m365_email_headers` plugin, renamed to `m365_email`.**

Onyx's [`backend/onyx/connectors/gmail/`](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/connectors/gmail) consists of two files: `__init__.py` + `connector.py`. A single `GmailConnector` extracts BOTH bodies (`_get_message_body()` decodes base64 text/plain MIME with 10MB cap) AND headers (`message_to_section()` captures from / to / cc / bcc / subject / date). **No `include_body` / `headers_only` flags — ingestion is unconditional.** The architectural signal: upstream treats body+headers as one document, headers as metadata.

For kairix: one `m365_email` plugin (drop `_headers` suffix) with config `body_ingest: false` (default) → `true` (opt-in).

Rationale:
- F39 sensitivity wiring is the same whether bodies or headers — classification logic lives once.
- Cutover safety: flipping `body_ingest: true` is the operator-controlled, default-safe cutover (per `feature-flag-architecture.md`). Two separate plugins would force re-registering in entry-points, doubling the F45/F46/F48 BDD/E2E surface.
- F36 cost: one feature file with body-on / body-off scenarios beats two duplicated feature files.
- Operator mental model: "headers or bodies" is one knob.

The renamed plugin still satisfies F35 (isolation) and F38 (Silver chunking centralised). Body flag flows into the Silver chunker's content-vs-metadata branch.

---

## 3. Notion teamspace policy mapping

**Finding: Notion API has no first-class teamspace primitive. Operator-config mapping is the right shape. Use a page-property convention as the override seam.**

Notion REST API documents pages with `parent.type ∈ {"workspace", "database_id", "page_id", "block_id"}` (per [`developers.notion.com/reference/page`](https://developers.notion.com/reference/page)) — **no `parent.teamspace_id`**. [Latenode community thread #12653](https://community.latenode.com/t/how-can-i-retrieve-notion-teamspace-details-via-api/12653) confirms: "retrieving teamspace details directly isn't currently possible". Notion MCP server's `notion-get-teams` tool is an MCP-server convenience, not in the underlying REST API. Pages inherit teamspace permissions implicitly ([Notion help](https://www.notion.com/help/intro-to-teamspaces)), but the API surfaces only the immediate parent page chain.

**Design decision — two-tier mapping with override**:

1. **Tier 1 — operator config** (primary): `connectors.notion.teamspace_sensitivity:` block maps teamspace **root page ID** (operators copy from URL: `notion.so/<teamspace-root-id>`) to one of `public` / `internal` / `confidential` / `restricted`. Connector walks `parent.page_id` upward at ingest, hits the configured root, applies the tier. Default = `internal` (per F39 explicit-write rule, no implicit-public fallback).

2. **Tier 2 — page-property override**: pages with a `kairix_sensitivity` select-property override inherited tier. Handles "confidential project doc inside an internal teamspace" without forcing teamspace splits.

Per-page metadata can't live alone (users won't tag every page reliably) — operator-config-by-default is right. Per-page override is the affordance that prevents the bulk-config trap. Both tiers written to `documents_media.sensitivity` at Bronze; F39 enforces explicit-write at chunk boundary.

---

## 4. GitHub PAT vs App scope for org subsets

**Finding: Recommend (a) GitHub App with installation-selected repos. Onyx's PAT-only choice is a known limitation, not the right model.**

Per [GitHub's fine-grained PAT GA announcement](https://github.blog/changelog/2025-03-18-fine-grained-pats-are-now-generally-available/) + [the introduction post](https://github.blog/security/application-security/introducing-fine-grained-personal-access-tokens-for-github/), Apps and fine-grained PATs share the same permission model but differ on critical operational axes:

| Axis | GitHub App | Fine-grained PAT | Classic PAT + filter |
|---|---|---|---|
| Tied to an individual? | No (org-owned) | Yes (user) | Yes (user) |
| Expiry | Never (rotating tokens) | ≤1 year | ≤1 year or never |
| Repo subset selection | Native (install UI) | Native | Operator-config filter |
| Survives user leaving org? | Yes | No | No |
| Audit trail | Per-app | Per-user | Per-user |

Onyx's GitHub connector ([`backend/onyx/connectors/github/connector.py`](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/connectors/github)) is hardcoded to PAT — long-running [issue #11013](https://github.com/onyx-dot-app/onyx/issues/11013) tracks the gap with reported "invalid JSON in callback" on App attempts. Onyx docs at [docs.onyx.app/admins/connectors/official/github](https://docs.onyx.app/admins/connectors/official/github) acknowledge this limitation. **Treat as historical drag, not prior art.**

**Design decision — App-first, fine-grained-PAT as fallback**:
- Default config = GitHub App; installation page handles repo selection.
- Fallback = fine-grained PAT for solo operators avoiding App ceremony.
- **Reject** classic PAT + operator-config repo filter: F44 doesn't block it, but puts the access-control decision in YAML instead of the GitHub-side authz primitive. If operator misconfigures filter, repos GitHub *did* grant the PAT leak into the index.

---

## 5. M365 calendar event.sensitivity field

**Finding: `sensitivity` is a first-class string property on `event`, returned by `/users/{id}/calendarView` without requiring `$select`. Direct wire to F39 with one nuance (Personal).**

Per [Microsoft Learn: event resource](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0), the event resource exposes:

```json
{ "sensitivity": "String" }
```

with enum values `normal`, `personal`, `private`, `confidential`. The property is in the default selection set — returned without `$select`. Belt-and-braces: still emit `$select=...,sensitivity,...` to guard against default-set changes.

**Proposed F39 mapping**:

| Outlook `sensitivity` | Outlook semantic | F39 tier | Rationale |
|---|---|---|---|
| `normal` | Default; no restriction | `internal` | Default-safe; matches "no marker present" implicit-internal convention |
| `personal` | User's personal item, not work-relevant | `confidential` + skip flag | **Nuance**: "Personal" isn't about confidentiality grade — it's "this is mine, not the org's". For kairix corporate-knowledge purposes, treat as not-for-ingest-or-confidential. Tag `confidential` + add config flag `skip_personal_events: true` (default on) so personal events are skipped at the connector boundary, not just demoted at Silver. |
| `private` | Hidden from delegates | `restricted` | Strongest exclusion tier |
| `confidential` | Encrypted / protected | `restricted` | Same tier as `private` |

Two-into-one collapse (`private` + `confidential` → `restricted`) loses information at Silver but matches kairix's four-tier model. Preserve original Outlook value in `documents_media.source_sensitivity` (free-form string) so a future five-tier expansion has the data; write kairix tier into `Chunk.sensitivity` per F39. Mapping operator-overridable via same config block as other source-tier mapping (consistency wins).

---

## Sources

- [getdex.com/docs/api-reference](https://getdex.com/docs/api-reference)
- [getdex.com/docs/api-reference/authentication](https://getdex.com/docs/api-reference/authentication)
- [zapier.com/apps/dex/integrations](https://zapier.com/apps/dex/integrations)
- [Onyx Gmail](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/connectors/gmail)
- [Onyx GitHub](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/connectors/github)
- [Onyx issue #11013](https://github.com/onyx-dot-app/onyx/issues/11013)
- [Onyx GitHub admin docs](https://docs.onyx.app/admins/connectors/official/github)
- [Notion: page reference](https://developers.notion.com/reference/page)
- [Notion: MCP-supported tools](https://developers.notion.com/guides/mcp/mcp-supported-tools)
- [Notion: teamspaces help](https://www.notion.com/help/intro-to-teamspaces)
- [GitHub: fine-grained PAT GA](https://github.blog/changelog/2025-03-18-fine-grained-pats-are-now-generally-available/)
- [GitHub: fine-grained PAT intro](https://github.blog/security/application-security/introducing-fine-grained-personal-access-tokens-for-github/)
- [Microsoft Learn: event resource](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0)
- [Microsoft Learn: list calendarView](https://learn.microsoft.com/en-us/graph/api/user-list-calendarview?view=graph-rest-1.0)
