## January 21, 2026

### New

- **dbt platform**
  - **Favorites are now available in Catalog**: Add resources to favorites and organize your frequently accessed resources in the Catalog navigation.

- **Connectivity / private networking**
  - **New v3 API endpoint to fetch a specific PrivateLink endpoint**: You can now retrieve individual PrivateLink endpoints by ID, enabling better automation and troubleshooting workflows.

### Enhancements

- **dbt platform**
  - **Run artifacts are now searchable**: Find specific artifacts faster in run history with the new artifacts search box and improved empty states.
  - **Webhooks editor is more stable**: The webhook form no longer resets while job options are loading, and server-generated fields now display reliably after creation.
  - **Fusion onboarding completion card can be dismissed**: After completing the Fusion onboarding checklist, you can now dismiss the card and it will stay dismissed.
  - **Cross-project lineage is now generally available**: Cross-project lineage is now enabled for all applicable accounts.

- **Catalog & Search**

  - **Improved Catalog search relevance and performance**: Enhanced search scoring and matching provides more accurate results, with better column matching and highlighting for large catalogs.
  - **Search results are refreshed when column metadata changes**: Column name and description updates now automatically trigger re-indexing, ensuring search results stay current.
  - **Search typeahead includes "View all results"**: Quickly access full search results from the typeahead dropdown with the new footer link.
  - **Cleaner environment dropdown behavior**: The environment selector now only shows "Staging" when your account has projects with a staging environment configured.

- **Studio IDE**
  - **Clearer error messages when fetching dev credentials and defer state**: IDE-related endpoints now return more specific and helpful error messages for common configuration issues and timeouts.
  - **Studio console and command log viewer improvements**: Enhanced command log viewer with improved download capabilities and more consistent error log viewing.

### Fixes

- **AI-assisted workflows**
  - **Enhancement:** [dbt <Constant name="copilot" />](/docs/cloud/dbt-copilot) adds missing column descriptions more accurately. <Constant name="copilot" /> generated documentation now correctly detects column names across various `schema.yml` files, adds only missing descriptions, and preserves existing ones.

- **Catalog & lineage**
  - **Fixes missing auto-generated exposures in model lineage**: Auto-generated exposures now appear correctly in lineage views.
  - **Catalog search no longer errors when a warehouse connection name is missing**: Search now handles missing connection names gracefully without causing errors.
  - **Improved security: malformed identity headers are rejected cleanly**: Requests with invalid authentication tokens now fail safely with clear error messages.

- **Studio IDE**
  - **Command status is more reliable when Cloud CLI invocation data expires**: Commands that can't be fetched are now properly marked as failed instead of staying in a "running" state.

- **APIs**
  - **Jobs API deferral validation is stricter and clearer**: Job deferral settings are now validated to ensure the deferring job and environment exist within the same account, with improved error messages.

### Behavior changes

- **dbt platform**
  - **Account Insights default page size changed to 5 rows**: Tables in Account Insights now display 5 rows per page by default (previously 10).

- **Webhooks**
  - **Webhook timestamps are now consistently UTC RFC3339 with `Z`**: All webhook timestamp fields (`run_started_at`, `run_finished_at`, `timestamp`) now use UTC with `Z` suffix and higher precision. Missing/invalid timestamps emit `1970-01-01T00:00:00Z` instead of empty strings. Update webhook consumers if needed.
  - **Webhook `run_status` string changed from `Error` to `Errored`**: Update webhook consumers that parse this status value strictly.

- **Runs / ingestion**
  - **Very large exposure sets are now limited during ingestion**: Projects with more than 5,000 exposures will skip exposure ingestion to prevent performance issues. All other artifact ingestion continues normally. Contact support if you need to increase this limit.