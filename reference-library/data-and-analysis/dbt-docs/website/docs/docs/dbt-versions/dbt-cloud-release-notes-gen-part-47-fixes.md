## Fixes

### dbt Copilot

- **Consistent usage limit messaging in Insights and Studio IDE**: When users hit the usage limit, dbt disables Copilot and shows a clear message, including the reset date when available.

### Studio IDE

- **Git status decorations registered once**: Fixed duplicate Git status decorations in the file tree that could cause visual issues and performance impact.

- **Avoid automatic pull on primary branch**: Studio IDE no longer runs an automatic pull on the primary branch to reduce unexpected changes during development.

- **Clearer file operation validation errors**: File operations now return structured validation errors and explicitly reject names that exceed operating system limits.

- **More reliable command log refresh and finalization**: Command logs for the dbt Cloud Command Line Interface (CLI) are refreshed and finalized more reliably.

### Run Automation

- **Correct account attribution for automatically triggered runs**: Scheduler triggered runs now include account context, improving run attribution and preventing some downstream triggers from running without proper context.

- **Reject malformed account identifiers for exposure events**: Exposure generated events now validate that account identifiers are numeric before triggering follow on automation.

### Webhooks

- **More compatible run completion payload for canceled and errored runs**: Webhook payloads now include consistent completion and error timestamps, and canceled runs include a canceled timestamp and normalized status.

- **Restored dual dispatch for some failure and completion triggers**: When both failure and completion triggers are configured, errored runs may generate two webhook deliveries to match legacy behavior.

### dbt Project Metadata

- **Manifest Ingestion: Accept functions section in manifest.json**: Ingestion now accepts the `functions` section (for example, Snowflake user defined functions (UDF)) to prevent parse failures on newer manifest schemas.

- **Macro Metadata: More consistent timestamps and argument comparison**: Macro metadata persistence now uses more consistent Coordinated Universal Time (UTC) timestamps and improves argument comparison to reduce noisy or incorrect macro updates.