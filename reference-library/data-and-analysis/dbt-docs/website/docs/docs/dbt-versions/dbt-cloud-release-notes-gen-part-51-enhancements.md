## Enhancements

### dbt platform

- **dbt platform: Fusion eligibility and compatibility indicators in setup flows**: Improves Fusion setup by showing “Fusion compatible” indicators during connection setup.

- **dbt platform: Compare Changes shows partial success warnings**: When Compare Changes subqueries fail, the experience now surfaces a partial success state with expandable warning details to make troubleshooting faster.

- **dbt platform: In-progress run logs preserve text selection**: Improves log usability during in-progress runs by preserving text selection while logs auto-refresh and rerender.

- **dbt platform: Job completion trigger job picker search**: Adds server-side search and clearer loading and empty states to the job picker for job-completion triggers.

- **dbt platform: Job artifacts content types and downloads**: Improves artifact handling for job documentation and run artifacts by strengthening HTML detection, defaulting empty paths to `index.html`, and returning clearer `Content-Type` and download filenames.

- **dbt platform: Private Endpoints API listing and pagination improvements**: Improves Private Endpoints API v3 list behavior with validated query parameters, filtering, limit and offset pagination, and `connection_count` in responses.

### Studio IDE

- **Studio IDE: Format file more reliable in subdirectories**: Improves formatting reliability by consistently using the active editor content and a stable repo-relative path when invoking formatting.

- **Studio IDE: Better stability for tabs and Git operations**: Reduces errors when working with non-file tabs and improves robustness around tab-close and Git checkout flows.

- **Studio IDE: Sidebar layout improvements for embedded panels**: Improves embedded panel sizing to reduce clipping and scrolling issues in the sidebar.

- **Studio IDE: Fusion prompts reflect actual eligibility**: Improves Fusion banners and prompts by checking project eligibility via a Fusion status endpoint to reduce confusing prompts for ineligible projects.

### Catalog and Discovery

- **Catalog: Improved cross-project lineage for dbt Mesh**: Improves cross-project lineage (“public ancestors”) computation to better match expected external lineage boundaries in dbt Mesh experiences.

### Insights

- **Insights: More reliable Copilot Agent requests and context handoff**: Standardizes Copilot Agent requests to the API and includes active tab content as context to improve reliability of agent runs and handoff.