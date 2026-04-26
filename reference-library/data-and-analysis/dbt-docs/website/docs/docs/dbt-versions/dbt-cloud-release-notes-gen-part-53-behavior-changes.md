## Behavior Changes

### dbt platform

- **dbt platform: Fusion default dbt version selection more restrictive**: During connection setup, the default dbt version now only defaults to `latest-fusion` when the selected adapter is Fusion-compatible and the project and account are eligible.

- **dbt platform: dbt version enforcement now project-aware**: dbt version “allowed version” checks now account for `project_id` across jobs and environments, including Application Programming Interface (API)-triggered runs, improving correctness for overrides and automatic mapping to allowed equivalents when possible.

- **dbt platform: Connected app refresh tokens now last 7 days**: Refresh token expiration for connected app OAuth flows increased from 8 hours to 7 days, reducing re-authorization frequency.

### Studio IDE

- **Studio IDE: File stat timestamps now milliseconds**: File stat responses now return modified time and created time as integer milliseconds since epoch instead of float seconds; integrations consuming these endpoints may need to adjust.

- **Studio IDE: Language Server Protocol deferral controls expanded**: The Language Server Protocol (LSP) websocket now supports `defer_env_id` to defer against a specific environment and `no_defer=true` to explicitly disable deferral.

- **Studio IDE: Deferral toggle applied more consistently to Language Server Protocol connections**: When “defer to production” is turned off, the Studio Integrated Development Environment (IDE) now passes `no_defer=true` to align editor intelligence with the selected deferral behavior. (Language Server Protocol (LSP))

### Catalog

- **Catalog: Source freshness outdated status removed**: The freshness status value `outdated` was removed; unconfigured freshness is now handled explicitly as `unconfigured`, and sources will no longer report `outdated`.

- **Catalog: Rows per page selector removed from tables**: The rows-per-page selector was removed, and pagination now uses a fixed page size.

### Orchestration and Run Status

- **Orchestration: Cached and stale outcome status mapping updated**: Cached nodes are now consistently surfaced as Reused with clearer reasons, and stale outcomes are treated as errors, which can change the statuses operators see in run output and telemetry.