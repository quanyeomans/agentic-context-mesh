## Enhancements

### Orchestration and Run Status

- **Run retries support dbt Fusion runs:** You can now retry failed runs as long as your environment is on dbt Core version `1.6` or higher or dbt Fusion.

### Integrations

- **More reliable Slack notifications:** Slack channel discovery and notifications now retry on Slack rate limits to reduce dropped messages during busy periods.

### APIs, Identity, and Administration

- **Improved OpenAPI typing for large integers:** OpenAPI schemas now mark 64-bit integer fields as `format: int64` to improve generated client types.

- **Clearer credentials schemas:** Credentials OpenAPI docs now use a `type` discriminator (`postgres`, `redshift`, `snowflake`, `bigquery`, and `adapter`) to improve code generation and request validation.