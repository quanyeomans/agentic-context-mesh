## December 24, 2025

### New

- **AI Codegen**
  - **File-aware LangGraph agents**: Analysts can now drop `@path` references in the bundled CLI to stream local files into `/private/v1/agents/run`, which are auto-rendered as text inside the run so copilots have the exact config or SQL snippet you referenced.  


- **dbt platform**
  - **Slack Copilot feedback loops**: Copilot replies now carry inline "Did that answer your question?" buttons, so you can rate answers without leaving Slack.  

- **Codex workflows**
  - **Databricks cost tracking for Model Cost Over Time**: A Databricks history provider and DBU-based cost query now surface daily model cost alongside Snowflake coverage, so Databricks tenants get unified FinOps reporting.  

- **Canvas**
  - **CSV upload GA**: The CSV upload endpoint is now generally available.

### Enhancements

- **Cloud artifacts**
  - **Better similar-model suggestions**: Attachment workflows now only recommend meaningfully related models.  

- **dbt platform**
  - **Unified SSO & SCIM admin**: Settings consolidate SSO + SCIM, add an empty state for auto-generated slugs, and render read-only login URLs so admins can start configuration without touching slug fields.  
  - **SCIM token management polish**: Token tables gain fixed pagination, inline search, consistent iconography, and clearer deletion warnings to avoid accidental cuts to live integrations.  
  - **Twice the per-environment custom variables**: The v3 API/UI now allow up to 20 scoped environment variables before enforcing limits, giving larger projects more room for secrets.  

- **Canvas**
  - **Dialect-aware projection SQL**: SELECT * RENAME/EXCEPT support now respects each warehouse's syntax using schema metadata, so SQL previews and column metadata stay accurate across Snowflake, Databricks, BigQuery, and Redshift.  

### Fixes

- **dbt platform**
  - **Webhook editor keeps job selections**: Default values are cached after the first render and stop resetting once the user edits the form, eliminating accidental job-list clearing while tabbing through fields.  

- **Codex GraphQL**
  - **Exposure parents mirror the manifest**: `parentsModels` and `parentsSources` now derive from the manifest's `parents` list, so exposures with mixed upstreams display complete lineage in both the GraphQL API and UI.  


### Behavior changes

- **dbt platform**
  - **Legacy Cost Management UI retired**: All cost management pages and hooks were removed, and platform metadata credentials now only expose catalog ingestion and Cost Insights toggles, eliminating dead-end controls.