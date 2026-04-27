## Enhancements

### Studio IDE

- **Reduced conflicts across multiple tabs**: Studio IDE can pause the Language Server Protocol (LSP) in background tabs and resume on return to improve stability when the editor is open in more than one tab.

- **More informative header and more editor space**: Adds a Visual Studio Code-style header showing a dbt badge and current project name, with an option to hide surrounding chrome for more editor space. Please contact your account manager to enable.

- **Clearer file and folder creation errors**: Surfaces more actionable filesystem errors (for example, name too long and file-is-a-directory) instead of generic failures.

- **Copy relative path**: Adds a Copy Relative Path action that respects `dbt_project_subdirectory` for quicker navigation and sharing.

- **Friendlier lineage error messages**: Improves user-facing errors for lineage failures (including server errors and cases where upstream returns HTML instead of JSON).

- **More reliable private connectivity selection**: Improves private endpoint filtering by adapter type and updates Studio IDE to use the correct version 3 private endpoints endpoint.

### Canvas

- **More reliable Add Sources CSV uploads**: Improves Comma-Separated Values (CSV) upload progress, resume behavior, and common error handling during Add Sources.


### Catalog

- **Faster and more usable lineage for large projects**: Improves directed acyclic graph (DAG) performance by rendering only visible elements and improving layout for disconnected nodes.

- **Safer search result interactions**: Improves keyboard and hover behavior in the search dropdown and avoids showing stale results while searches are loading.

### dbt platform

- **More informative user invite statuses**: This change shows clearer invite status (invitation sent and invitation accepted) and supports accepted, login pending for Single Sign-On (SSO).

- **Unpaid billing banner enabled by default**: The unpaid billing banner is no longer feature-flagged and will display when applicable, while billing link visibility remains permission-based.

- **System for cross-domain identity management (SCIM)**: Bug fixes and improvements related to managed invites for easier processing.

### dbt Copilot and agents

- **Streaming control for server-sent events**: Adds Server-Sent Events (SSE) streaming control so clients can choose chunk streaming or message streaming. This enables more responsive Copilot experiences in environments that support streaming.

- **More reliable similar models requests**: Improves responsiveness for AI Similar Models and Similar Sources requests by enforcing tighter embedding and database timeouts aligned to request deadlines.  Users should see faster, more consistent results when exploring related models.

- **dbt Copilot: Improved bring your own key error handling**: Categorizes OpenAI failures with Bring Your Own Key (BYOK) awareness so BYOK failures return the expected 424-class behavior instead of generic 500-series errors.  This makes it easier to diagnose and resolve key or configuration issues.

- **Expanded dbt Model Context Protocol tooling**: Updates dbt Model Context Protocol (MCP) tooling, including adding `get_all_macros` and improving error categorization, enabling more accurate responses.