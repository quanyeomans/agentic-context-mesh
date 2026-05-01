## Fixes

### Orchestration and Run Status

- **More reliable job search:** Searching jobs with numeric terms (for example, `12`) no longer triggers API validation errors, so you can load job lists reliably.

- **Clearer cross-project publication errors:** When dbt platform cannot fetch a publication artifact for an upstream project declared in `dependencies.yml`, you now see which project is missing an artifact and guidance to run the upstream environment at least once.

### Integrations

- **More accurate Microsoft Teams notification triggers:** Microsoft Teams notifications now use the correct trigger event type for each notification, so you see the expected run outcome context in the message.

### APIs, Identity, and Administration

- **More accurate error responses during permission checks:** You now receive more accurate errors from permission checks, and underlying service errors surface instead of being reported as authorization failures.

### Deployment and Configuration

- **Clearer private endpoint validation errors:** Creating a private endpoint now returns a `400` error with a clear message when `snowflake_output` is malformed or not valid JSON.