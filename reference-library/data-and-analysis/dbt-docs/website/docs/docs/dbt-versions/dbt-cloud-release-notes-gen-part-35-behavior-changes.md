## Behavior Changes

### Orchestration and Run Status

- **`versionless` dbt version is no longer accepted**: dbt platform now treats `versionless` as deprecated and updates existing environments and jobs to use `latest`. If you set `dbt_version` in an API integration or automation, update it to send `latest` instead.

### Webhooks

- **Account identifier required for run-based notifications**: If you send events that include a `run_id`, you must also provide an `account_identifier` so the service can validate and resolve the correct account before dispatch. If `account_identifier` is missing, the event fails instead of falling back to a `run_id`-only lookup.