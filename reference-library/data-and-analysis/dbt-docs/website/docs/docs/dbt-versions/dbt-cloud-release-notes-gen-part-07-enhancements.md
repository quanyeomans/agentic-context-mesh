## Enhancements

### Catalog

- **Health and run status search filters**: The `AccountSearchQueryFilter` input now accepts `health` and `runStatus` filter arrays. Use `health` to narrow results by health status (`healthy`, `caution`, `degraded`, or `unknown`) and `runStatus` to filter by last run outcome (`success`, `error`, `skipped`, or `reused`). Multiple values within each filter are combined with `OR` logic.

- **Health-aware search ranking**: Healthy dbt resources (those with no detected issues) now rank higher in search results than resources with unresolved issues when text relevance is otherwise equivalent.

### Studio IDE

- **Keyboard shortcut to open Commands tab**: Press `Ctrl+\`` to open the Commands tab directly from the editor.

### Orchestration and run status

- **Clearer Fusion job eligibility messages**: Fusion eligibility reason messages are rewritten to be shorter and more actionable. For example, unsupported adapters now read "This job uses an adapter that's not currently available on the Fusion engine" and jobs not on Latest now read "This job uses a dbt version that's not tested for Fusion eligibility."

- **Fusion eligibility confirmation modal**: Clicking "Run once on Fusion" on a job now opens a confirmation modal before triggering the run, showing the environment name and a warning that job commands will execute in that environment.

- **Improved `dbt ls` and `dbt list` run log status (dbt Fusion engine only):**: Run steps that execute `dbt ls` or `dbt list` now show node results with a no-op status instead of "unknown," reducing confusion in run logs for list operations.

### dbt platform

- **More descriptive Fusion readiness toggle**: The account-level setting to enable Fusion readiness and upgrade features now has an updated label ("Enable Fusion readiness & upgrade features") and a more detailed description explaining what the setting allows administrators and developers to do.

- **Debug on Fusion navigates with version override**: The "Debug on Fusion" button (previously "Debug manually") on failed Fusion run banners now sets your personal `DBT_DEVELOP_CORE_VERSION` override to `latest-fusion` before opening Studio IDE, ensuring you open the IDE on the Fusion engine. A loading state is shown while the override saves, and an inline error is displayed if the save fails.

### Deployment and configuration

- **Private endpoint connectivity status column renamed**: The "Status" column in the private endpoints list is renamed to "Connectivity status" for clarity.

- **Snowflake private endpoint validation shows specific missing fields**: When pasting Snowflake Private Link configuration output, the validation error now lists the specific required fields that are missing (for example, `privatelink-account-url`) rather than a generic message. Valid output now also shows a success indicator.

- **YAML credential fields now accept array values**: Environment credential and connection forms that accept YAML Extended Attributes (for example, Redshift `db_groups`) now correctly validate arrays as values. Previously, array values were incorrectly rejected during client-side validation.

### Integrations

- **Snowflake PrivateLink supports reusing existing interface endpoints**: When creating a Snowflake PrivateLink connection, you can now supply an optional `interface_endpoint_id` to attach a new profile to an existing interface endpoint rather than always creating a new one. The endpoint must be in `Available` status; a `409 Conflict` is returned otherwise. Contact your account manager to enable.