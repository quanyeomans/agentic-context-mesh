# google_calendar — Google Calendar connector

First-party `SourceConnector` for one Google Calendar (one
`calendar_id`, defaulting to `primary`). Streams events via the
Google Calendar API v3 `events.list` endpoint and persists the
returned `nextSyncToken` as the incremental-sync cursor.

## Status

Ships flag-gated **OFF** — Google Workspace OAuth credentials are not
yet provisioned in the operator's Key Vault. Tracked GH #356. Until that lands,
flipping `topology_v2_google_calendar` ON is a no-op because
`make_connector` raises with a fix pointer when `access_token` is
empty.

## Credentials

OAuth 2.0 access token resolved via the operator's secret-resolution
boundary (KV-backed in production). The connector never touches the
OAuth token endpoint itself.

Required Google scope: `https://www.googleapis.com/auth/calendar.readonly`.

The operator's KV entry (once #356 lands) carries:

- `access_token` — OAuth 2.0 bearer

The connector itself does not refresh the token — that responsibility
sits with the secret-rotation pipeline in the operator's Key Vault.

## Config keys

| Key | Default | Notes |
| --- | --- | --- |
| `access_token` | (required) | OAuth 2.0 bearer; resolved via secrets, not env vars |
| `calendar_id` | `"primary"` | Google calendar id; operator can scope to a non-primary calendar |
| `sensitivity` | `"internal"` | One of `public` / `internal` / `client-confidential` / `personal`; use `personal` for personal calendars per ADR-005 |
| `window_days_back` | `30` | Initial-sync window in days; subsequent syncs use the returned `nextSyncToken` |
| `page_size` | `250` | `events.list` `maxResults`; Google caps at 2500 |

## Feature flag

`topology_v2_google_calendar` — introduce stage, default OFF. Defined
in `kairix/core/features/registry.py`. Retires at
`v2026.11.30` (6 months from landing per F51).

The dispatcher (`kairix.worker.dispatch_google_calendar_sync`) reads
the flag at the connector-selection boundary — when OFF, the plugin
never runs even if listed in `kairix.config.yaml`. When ON, the
connector is selected via the standard config + entry-point shape.

## Recurring events

Per ADR-028 §"Calendar event" recommendation: the connector stores
the master event plus the RRULE in metadata, **not** N per-occurrence
documents. Concrete behaviour:

- `singleEvents=false` on every `events.list` call (Google's default).
- The `recurrence` array (RRULE / EXRULE / RDATE / EXDATE strings)
  surfaces on `SourceMetadata.properties.recurrence_rule`.
- `status="cancelled"` events are skipped (no `ChangeEvent` emitted).

The retrieval-side time filter uses the RRULE to compute "events next
week" without inflating the index with near-duplicates.

## SyncToken expiry

Google returns 410 Gone when a persisted `nextSyncToken` is too old.
The connector catches `SyncTokenExpiredError` and transparently runs
a fresh initial sync from `now - window_days_back`.

## Tests

- Contract: `tests/contracts/test_google_calendar_protocol.py` (F43)
- Integration:
  - `tests/integration/test_google_calendar_metadata_propagation.py` (F65)
  - `tests/integration/test_google_calendar_rate_limit.py` (F64)
  - `tests/integration/test_google_calendar_failure_modes.py` (F68)
  - `tests/integration/test_feature_flag_topology_v2_google_calendar.py` (F54)
- BDD: `tests/bdd/features/connector_google_calendar.feature` (F45 happy path)
  - `tests/bdd/features/feature_flag_topology_v2_google_calendar.feature` (F54 both-branch)

All tests run against a scripted `GoogleCalendarClient` injected via
the `client_factory` DI seam — no live Google API calls fire during
the test suite.

## Live enable runbook

Once #356 provisions Google Workspace OAuth credentials in
the operator's Key Vault:

1. Confirm the secret exists at the expected key.
2. Add a `google_calendar:` block to `kairix.config.yaml` with the
   secret reference + `calendar_id`.
3. Run `kairix features set topology_v2_google_calendar=true`.
4. Capture a pre-flip baseline via
   `scripts/cutover/capture_baseline.py`.
5. Soak 24 hours.
6. Diff via `scripts/cutover/diff_baseline.py` and promote stage or
   rollback per the cutover protocol in
   `docs/architecture/feature-flag-architecture.md`.
