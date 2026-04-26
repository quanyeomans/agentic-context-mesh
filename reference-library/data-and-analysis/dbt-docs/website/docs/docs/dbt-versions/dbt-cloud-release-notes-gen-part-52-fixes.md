## Fixes

### dbt platform

- **dbt platform: Webhook form editing more resilient**: Improves webhook subscription editing reliability with asynchronous data and fixes a multiselect focus issue that could cause accidental option selection.

- **dbt platform: Run warning emails render correctly**: Fixes HTML email markup that could break rendering for run warning notifications.

- **dbt platform: Profiles URLs moved under project dashboard** Profile create and view routes now live under `/dashboard/:accountId/projects/:projectId/profiles/...`, which may affect bookmarks and direct links.

### Studio IDE

- **Studio IDE: Cleaner command history list**: Removes hidden background commands (such as listing and parsing commands) from command history to reduce noise for users.

- **Studio IDE: More reliable inline compile and show output**: Improves robustness of inline compile and show output attachment, including cases with tricky quoting and newlines, reducing missing results during interactive use.

- **Studio IDE: More reliable log downloads for dbt commands**: Fixes log download behavior so downloads correctly serve either the active `dbt.log` or the finalized compressed log.

- **Studio IDE: More reliable artifact uploads to Microsoft Azure Blob Storage**: Fixes edge cases where gzipped artifacts (such as manifests) could fail to upload due to upload stream handling, improving upload reliability.

- **Studio IDE: More stable language server protocol (LSP) sessions in workers**: Reduces noisy disconnect and cleanup errors when multiple websocket connections and processes map to the same invocation, improving session stability. 

### Catalog

- **Catalog: Search highlighting displays correctly with multiple matches**: Fixes search result highlighting when the backend returns multiple highlights per field, improving readability of matches. Updates search highlights to display as compact badges with counts for easier scanning of results.

- **Catalog: Environment filtering more accurate in search results**: Improves environment-scoped Catalog search filtering by using merged environment identifiers and preserving warehouse-only assets via a dedicated sentinel value.

- **Catalog: Public models return empty list when none exist**: Improves behavior for environments with no public models by returning an empty list instead of falling into follow-on query logic.

### Copilot

- **Copilot: More reliable model context protocol (MCP) connections during long tool calls**: Improves keep-alive behavior so connections shut down cleanly when the client disconnects, reducing noisy failures.

- **Copilot: Semantic Layer tools only offered when available**: Prevents failing tool calls by hiding Semantic Layer tools when the Semantic Layer is not available for the user or environment.

- **Copilot: More accurate HTTP error responses**: Improves error reporting by walking wrapped exceptions and exception groups to return the most specific status code and detail available.

- **Copilot: Empty Tool Outputs No Longer Cause Failures**: Treats empty tool outputs as valid results (for example, “no matches”) to reduce unnecessary “tool call failed” errors.