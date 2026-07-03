# google_drive connector

First-party kairix connector for Google Drive v3. Pulls envelope plus
binary content for every file visible to the configured OAuth grant,
then hands the bytes off to the kairix extractor registry for
per-format extraction.

## What it does

For each configured corpus, the connector:

1. Resolves an OAuth bearer via `kairix.secrets.get_secret(
   "connector-google-drive-access-token")`.
2. Calls `GET /drive/v3/changes/startPageToken` on first sync to seed
   the cursor; subsequent ticks resume from the persisted
   `newStartPageToken`.
3. Walks `GET /drive/v3/changes?pageToken=…` page-by-page (with the
   Shared-Drive enumeration flags `supportsAllDrives`,
   `includeItemsFromAllDrives` and `corpora=allDrives`, so files on
   Shared / Team Drives are included, not just My Drive), emitting one
   typed `ChangeEvent` per file (created / modified / deleted).
4. Lazy-fetches the file content when the orchestrator calls
   `fetch(item_id)`, branching on the file's `mimeType`:
   - Google-native types (`application/vnd.google-apps.*` — Docs /
     Sheets / Slides) export via
     `GET /drive/v3/files/{id}/export?mimeType=…` (Docs → `text/plain`,
     Sheets → `text/csv`, Slides → `text/plain`).
   - All other (binary) types download via
     `GET /drive/v3/files/{id}?alt=media`.
   Both paths carry `supportsAllDrives=true` so Shared-Drive files
   resolve.
5. Surfaces envelope metadata (`lastModifyingUser.emailAddress`,
   `modifiedTime`, `webViewLink`) on every emitted chunk via
   `metadata_for` per ADR-021.

## Required credentials

The operator-side KV provisioning is tracked under
[GH #356](https://github.com/three-cubes/kairix/issues/356) and is out
of scope for this connector's code. Once that issue lands, the
following secret name must be present in the operator's Key Vault:

| Logical name | Env-var form | Purpose |
| --- | --- | --- |
| `connector-google-drive-access-token` | `CONNECTOR_GOOGLE_DRIVE_ACCESS_TOKEN` | OAuth bearer for the configured workspace user (or service-account impersonation) |

The credential must carry `drive.readonly` scope (or broader). The
refresh-token rotation flow runs out of band — when the access token
expires, Drive returns 401 and the connector raises
`CredentialExpiredError`; the framework's cc_pair lifecycle catches
this and transitions the cc_pair to a credential-renewal state for
operator action.

## Config

In `kairix.config.yaml`:

```yaml
connectors:
  - name: google_drive
    config:
      corpora:
        - corpus_id: workspace-default
          display_name: Engineering workspace
      default_sensitivity: internal  # one of public / internal / client-confidential / personal
```

`corpora` is a non-empty list. Each entry is either a string
(`corpus_id` only) or a mapping with `corpus_id` plus optional
`display_name`. `default_sensitivity` defaults to `internal`.

## Flag gating

The connector is gated by the `topology_google_drive` feature flag.
Default OFF — flipping ON requires the operator to:

1. Confirm the KV secret from GH #356 is provisioned.
2. Confirm the OAuth grant carries `drive.readonly`.
3. Add `google_drive` to `connectors[]` in `kairix.config.yaml`.
4. Restart the worker.

See [`docs/architecture/feature-flag-architecture.md`](../../../docs/architecture/feature-flag-architecture.md)
for the capture-flip-soak-gate cutover protocol.

## Out of scope (v1)

- Per-actor sharing-ACL sync. v1 applies the operator-declared
  sensitivity tier uniformly across the corpus.
- Per-Shared-Drive corpus isolation. Shared (Team) Drive *items* are
  now enumerated and exported (see above), but v1 still maps every
  configured corpus through a single connector instance — a future
  Wave-E branch adds per-Shared-Drive container cursors.
