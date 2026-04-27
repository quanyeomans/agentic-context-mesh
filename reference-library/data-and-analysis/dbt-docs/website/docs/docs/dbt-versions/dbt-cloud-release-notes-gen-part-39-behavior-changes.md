## Behavior Changes

### Webhooks

- **Updated job run event field presence and status normalization**: Webhook payloads now include `runFinishedAt` only for completed events and `runErroredAt` only for errored events; canceled runs no longer include `runCanceledAt`, and run status is normalized from Cancelled to Canceled. Also note that enabling JSON preserve order can change key ordering, so consumers should parse JSON rather than string-compare payloads.

### Insights APIs

- **Optional source freshness expiration windows**: Source freshness expiration windows can optionally derive from each source’s freshness criteria rather than a fixed window. You must enable in your deployment.

### Deployment and Configuration

- **Source ingestion may skip sources for extremely large manifests in Catalog**: For very large `manifest.json` files, ingestion may strip sources above a configurable threshold to prevent out of memory failures. Set `SOURCE_INGESTION_THRESHOLD=0` if you must always ingest sources regardless of size.

- **Removed deprecated object storage settings in Studio IDE**: Deprecated settings `project_storage_bucket_name` and `project_storage_object_prefix` have been removed. Migrate to `object_storage_bucket_name` and `object_storage_object_prefix`.