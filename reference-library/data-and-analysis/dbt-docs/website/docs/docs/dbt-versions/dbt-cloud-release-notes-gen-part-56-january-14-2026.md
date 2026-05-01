## January 14, 2026

### New

- **dbt platform**
  - **Fusion migration readiness endpoint**: Added an API endpoint to determine whether a project is eligible for Fusion migration.

### Enhancements

- **Copilot and AI**
  - **More resilient agent runs**: Agent tool execution errors now return structured responses instead of failing the entire run.
  - **Better project context retrieval**: Agent toolsets include additional retrieval and search capabilities for more relevant responses.
  - **Improved Azure OpenAI verification**: Azure OpenAI connection verification now uses GPT-5-compatible parameters for GPT-5 deployments.
  - **BYOK for Azure OpenAI**: Added support for Azure Foundry URLs with automatic endpoint parsing to reduce setup friction.

- **Insights and Catalog**
  - **Semantic Layer querying now generally available (GA)**: Build SQL queries against the Semantic Layer without writing SQL code.
  - **Improved search relevance**: Search scoring prioritizes exact and multi-term matches more strongly, with better highlighting and column-description matching.
  - **Catalog UX improvements**: Search labels are more consistent, and the embedded lineage view loads more responsively.

- **Studio IDE**
  - **Unified Studio IDE**: Studio now loads a single unified IDE package.
  - **Defer-to-production honors `defer-env-id` override**: Studio now respects `dbt-cloud.defer-env-id` settings when Cloud CLI runtime is supported.
  - **Improved log exporting**: Download and copy behavior for command logs is more consistent, including debug logs.
  - **Enhanced multi-edit support**: The IDE now supports multiple explicit edits in one request with safer validation.
  - **Clearer Cloud CLI session errors**: Session creation returns clearer error messages and guidance for setup issues.

- **dbt platform**
  - **Settings detail pages in resizable drawer**: Settings detail experiences now use an improved drawer-based UI.
  - **More resilient profile creation**: Profile creation now handles dependencies and failures more gracefully.
  - **Enhanced logging limits for in-progress runs**: Logs for in-progress runs are also limited by memory usage, in addition to the existing 1,000-line limit.

### Fixes

- **dbt platform**
  - **Profiles API clearing extended attributes**: The Profiles API now allows unsetting extended attributes by setting `extended_attributes_id` to null.
  - **Recently viewed more reliable**: Recently viewed entries now update atomically and retain the 5 most recent items.
  - **Run log tailing improvements**: Debug logs for completed runs now consistently fetch only the tail of the log.

- **Studio IDE**
  - **More reliable `show` and `compile`**: CLI flags to disable caching are now positioned correctly to avoid parsing issues.
  - **Canvas preview improvements**: Fixed argument ordering so `--no-defer` is interpreted consistently.


### Behavior changes

- **dbt platform**
  - **dbt v1.7 end-of-life**: dbt v1.7 is now labeled as end-of-life in version lifecycle messaging.