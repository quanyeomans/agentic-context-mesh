# `kairix.connectors.gmail` — Gmail SourceConnector

First-party SourceConnector for Google Workspace and personal Gmail
inboxes. Pulls every message in the authorised mailbox as one document
— body bytes plus envelope headers (Subject / From / To / Cc / Bcc /
Date / Thread / Labels) — via the Gmail REST API.

## Status

Behind the `connector_gmail` feature flag (introduce stage, default
**OFF**). The connector cannot be live-verified until the Workspace
OAuth credentials are provisioned into the operator's Key Vault — tracked under
[GH #356](https://github.com/three-cubes/kairix/issues/356).

## What it does

- One Gmail message → one kairix document.
- Body extraction: `text/plain` part preferred; `text/html` fallback
  stripped to plain text. 10 MB body cap (matches the Onyx default).
- Envelope metadata: Subject / From / To / Cc / Bcc / Date / Thread /
  Labels surface via `SourceMetadata`.
- Attachments: metadata only (filename / size / mime). Attachment
  bodies are out of scope — the Drive connector is the right home
  for those.
- Quoted reply chains: kept verbatim in v1 (`EmailThreadChunker` in
  ADR-028 G.1 will handle stripping later).

## Change detection

Uses the Gmail History API:
- Cold start: `users.getProfile` returns the current `historyId`; the
  connector persists that value as the initial cursor (Gmail's
  History API rejects values older than ~7 days, so cold-start does
  not backfill).
- Subsequent ticks: `users.history.list?startHistoryId=<cursor>`
  returns events strictly after the cursor; we surface one `created`
  ChangeEvent per `messagesAdded` event.

## Credentials needed

Resolved via `kairix.secrets.get_secret()`. All three are required for
the connector to run:

| Secret name | Purpose |
|---|---|
| `connector-gmail-client-id` | OAuth Client ID from the Google Workspace project |
| `connector-gmail-client-secret` | OAuth Client Secret |
| `connector-gmail-refresh-token` | Refresh token from the consent flow |

Optionally:

| Secret name | Purpose |
|---|---|
| `connector-gmail-access-token` | Live access token (tests; production refreshes per tick) |

## OAuth scopes

The authorised credential MUST hold:

- `https://www.googleapis.com/auth/gmail.readonly`

Read-only is sufficient for the v1 surface (no message mutation, no
draft creation, no label management). The narrow scope lets operators
roll out the connector under a per-mailbox consent without elevated
Workspace admin involvement.

## Config keys

In `kairix.config.yaml`:

```yaml
connectors:
  - name: gmail
    user_email: agent-alpha@example.com   # required: the authorised mailbox
    sensitivity: client-confidential       # optional: defaults to client-confidential
```

Valid sensitivity values: `public`, `internal`, `client-confidential`,
`personal`. The factory rejects any other value with an actionable
error message.

## Feature flags

- `connector_gmail` — gates the connector at the worker dispatch
  boundary. When OFF, the Gmail plugin never runs even if listed in
  the connectors config. When ON, the standard connector pipeline
  resolves the `gmail` plugin via its entry-point factory.
- `topology_v2_gmail` — gates the Wave E per-mailbox container surface.
  When OFF, `list_changes_for_container` delegates to the legacy
  single-cursor `list_changes`. When ON, the connector emits one
  Container per mailbox with its own `historyId` cursor.

Both flags default OFF; flip via `KAIRIX_FEATURE_CONNECTOR_GMAIL=true`
or via the config overlay. Soak cutover protocol applies — see
`docs/architecture/feature-flag-architecture.md` §7.

## Failure-mode contract (F68)

The Gmail client translates non-2xx responses into typed exceptions:

| Response | Typed error | Behaviour |
|---|---|---|
| 429 (+ Retry-After) | `ContainerTransientError` | Runner defers tick |
| 403 (`userRateLimitExceeded` / `rateLimitExceeded` / `dailyLimitExceeded`) | `ContainerTransientError` | Runner defers tick |
| 403 (other) | `InsufficientPermissionsError` | Operator action — revoked scope |
| 401 | `CredentialExpiredError` (after one retry) | Token rotation needed |
| 5xx | `ContainerTransientError` (60s default budget) | Runner defers tick |
| 4xx (other) | `ContainerTransientError` (no budget) | Per-item dead letter |

## Tests

- `tests/contracts/test_gmail_protocol.py` — F43 plugin contract.
- `tests/integration/test_gmail_metadata_propagation.py` — F65 envelope
  metadata propagates through Silver.
- `tests/integration/test_gmail_rate_limit.py` — F64 rate-limit signals.
- `tests/integration/test_gmail_failure_modes.py` — F68 typed errors
  for the full failure catalogue.
- `tests/integration/test_feature_flag_topology_v2_gmail.py` — F54
  both-branch coverage.
- `tests/bdd/features/connector_gmail.feature` — F45 happy path.
- `tests/bdd/features/feature_flag_topology_v2_gmail.feature` — F54
  both-branch BDD.
