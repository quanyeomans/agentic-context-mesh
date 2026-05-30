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
3. Walks `GET /drive/v3/changes?pageToken=…` page-by-page, emitting one
   typed `ChangeEvent` per file (created / modified / deleted).
4. Lazy-fetches the file binary via `GET /drive/v3/files/{id}?alt=media`
   when the orchestrator calls `fetch(item_id)`.
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

The connector is gated by the `topology_v2_google_drive` feature flag.
Default OFF — flipping ON requires the operator to:

1. Confirm the KV secret from GH #356 is provisioned.
2. Confirm the OAuth grant carries `drive.readonly`.
3. Add `google_drive` to `connectors[]` in `kairix.config.yaml`.
4. Restart the worker.

See [`docs/architecture/feature-flag-architecture.md`](../../../docs/architecture/feature-flag-architecture.md)
for the capture-flip-soak-gate cutover protocol.

## Out of scope (v1)

- Google-native file export. Files whose `mimeType` is
  `application/vnd.google-apps.*` (Docs / Sheets / Slides) require the
  `/export` endpoint instead of `alt=media`. v1 surfaces the native
  mime to the extractor registry and lets the extractor decide; a
  follow-up slice adds the export step.
- Per-actor sharing-ACL sync. v1 applies the operator-declared
  sensitivity tier uniformly across the corpus.
- Shared-drive enumeration. v1 supports one corpus per connector
  instance.
