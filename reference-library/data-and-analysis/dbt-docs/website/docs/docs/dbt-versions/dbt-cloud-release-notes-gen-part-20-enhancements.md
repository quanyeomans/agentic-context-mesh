## Enhancements

### Studio IDE

- **Consistent console pane default size**: The bottom console pane now opens at a preferred size of 33% of the available space, providing a more consistent default layout.

- **Faster text search results**: File search now reports results incrementally on a per-file basis rather than per-match line, reducing memory pressure and improving perceived responsiveness during large searches.

- **Smarter bulk-edit file handling**: When Studio IDE applies multi-file edits (for example, from dbt Copilot agent tasks), it now only updates editor models for files that are already open. Previously, every edited file was opened in a new tab, which cluttered the editor.

### Deployment and Configuration

- **Fusion migration enablement via project API**: You can now set `fusion_migration_enabled` on a project via the project update API. Enabling it requires the `fusion_readiness_write` permission, and the project must meet all readiness prerequisites (supported adapter, supported dbt version, a successful run, and eligible jobs).

- **Filter jobs by Fusion readiness**: The jobs list endpoint (`GET /api/v2/accounts/{account_id}/jobs/`) now accepts an `is_fusion_ready` boolean query parameter. When `true`, it returns only conformant or override-ready jobs; when `false`, it returns only non-ready jobs. You can also include `fusion_readiness` in the `include_related` parameter to surface Fusion readiness details alongside the job response.

- **Platform metadata credentials form opens immediately**: When adding platform metadata credentials for a connection, the credential form is now shown immediately instead of requiring you to click an "Add credentials" button first.