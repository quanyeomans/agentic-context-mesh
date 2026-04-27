## Fixes

### Catalog

- **Accurate resource counts on environment switch**: Fixes a bug where resource counts in the navigation tree were not refreshed when switching environments. You should now see up-to-date counts after changing the active environment.

### Orchestration and Run Status

- **Stuck runs are now cancelled**: A new cleanup job detects runs and run steps that have exceeded the maximum allowed duration and marks them as `CANCELLED`, preventing stale in-progress states from accumulating

### Semantic Layer

- **More reliable Snowflake connections after warehouse auto-resume**: The Semantic Layer Gateway now retries the initial connection when a Snowflake warehouse is waking up from auto-suspend, instead of failing immediately. You should see fewer connection errors when querying the Semantic Layer after a period of inactivity.

### APIs, Identity, and Administration

- **Large group permission sync no longer silently truncated**: Fixed an issue where group permission sync could miss updates for groups with many permissions.