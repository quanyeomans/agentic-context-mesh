# Apple iCloud CalDAV calendar connector

First-party connector that ingests calendar events from an Apple iCloud
account via the CalDAV protocol (RFC 4791 + RFC 6578). One connector
instance covers every calendar surfaced by one iCloud Apple ID; the
connector can optionally be scoped to a subset of calendar URLs via
the `calendar_ids` config.

## What it does

Each sync tick:

1. Discovers calendar collections via PROPFIND against
   `https://caldav.icloud.com` (cached for the process lifetime).
2. Runs a `<sync-collection>` REPORT per calendar (RFC 6578) with the
   persisted per-calendar sync token. Falls back to ctag-comparison
   when the server doesn't advertise sync-token support.
3. Emits one `ChangeEvent` per event observed since the last cursor —
   `created` for first-seen ids, `modified` for repeats, `deleted` for
   events marked `STATUS:CANCELLED` or returned with a CalDAV
   tombstone status.
4. Surfaces envelope metadata (`ORGANIZER`, `DTSTART`, `DTEND`,
   `LOCATION`, `RRULE`, `ATTENDEE`, `LAST-MODIFIED`) on the
   `SourceMetadata` payload so the search layer can boost recency,
   surface attendees as tags, and answer "what meetings did I have
   last week".

## What it requires

- An Apple iCloud account
- An **app-specific password** for that account (NOT the regular Apple
  ID password). See <https://support.apple.com/en-us/HT204397> for the
  Apple instructions on generating one.
- The Python `caldav` optional dependency:
  `pip install Kairix-agentic-knowledge-mgt[caldav]`

## How operators enable it

Apple CalDAV credentials are provisioned in the operator's Key Vault under:

- `apple-caldav-username` — the iCloud Apple ID
- `apple-caldav-access` — the app-specific password

Once the credentials are in KV, flip the flag and add the connector
to `kairix.config.yaml`:

```yaml
features:
  topology_v2_apple_caldav: true

connectors:
  - name: apple-caldav
    extractor: passthrough  # ICS rendered to text by the connector
    config:
      endpoint: https://caldav.icloud.com
      calendar_ids: []  # empty = all calendars; otherwise list URLs

credentials:
  - id: apple-caldav-icloud
    kind: basic_auth
    secret_name_prefix: apple-caldav
```

The connector resolves credentials via the standard
`kairix.secrets.get_secret` chain — env var → per-file secret →
bundle file → Azure Key Vault — so deployments without KV can drop
the values in `CONNECTOR_APPLE_CALDAV_USERNAME` +
`CONNECTOR_APPLE_CALDAV_PASSWORD` env vars (or per-file secrets at
`~/.config/kairix/secrets/apple-caldav-username` /
`apple-caldav-access`).

## What it does NOT do

- It does **not** accept the operator's primary iCloud password.
  App-specific passwords are the only documented Apple surface for
  CalDAV; passing the primary password fails with HTTP 401.
- It does **not** ship the live PVT path. The flag is default-OFF
  until soak runs against a production iCloud account confirm no
  rate-limit / sync-token drift.
- It does **not** ingest reminders or contacts — only `VEVENT`
  components from CalDAV calendars. Reminders use CalDAV `VTODO`;
  contacts use CardDAV (a different RFC).

## Operator how-to: rotate the app-password

If the existing app-password is revoked or compromised:

1. Sign in at <https://appleid.apple.com>.
2. Navigate to "Sign-In and Security" → "App-Specific Passwords".
3. Revoke the existing password; generate a new one labelled
   "kairix".
4. Update the `apple-caldav-access` secret in the operator's Key Vault.
5. Restart the kairix worker (or wait for the next sidecar refresh).

The connector retries the CalDAV connection on every tick, so the new
password takes effect within one sync interval (default 5 minutes)
once the secret rotates.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| HTTP 401 on every call | App-password revoked or operator changed primary password (which also revokes app-passwords) | Rotate the app-password per the operator how-to above |
| HTTP 503 + Retry-After | iCloud throttling — connector dead-letters the current tick; subsequent tick retries | Wait one sync interval; no operator action required |
| Empty calendar list | Operator's account has no shared calendars OR `calendar_ids` filter doesn't match any discovered URL | Drop the `calendar_ids` filter to confirm discovery works |
| Events missing `RRULE` metadata | Event uses iCloud's "Custom Repeat" UI which emits RDATE instead of RRULE | Known limitation; v0.2 will surface RDATE lists in `properties` |

## References

- RFC 4791 — CalDAV (the base spec)
- RFC 6578 — `<sync-collection>` REPORT
- Apple developer docs — iCloud Calendar implementation notes
- `kairix/connectors/apple_caldav/connector.py` — the canonical
  source-of-truth for this connector's behaviour
- `tests/bdd/features/connector_apple_caldav.feature` — the
  behavioural contract under test
