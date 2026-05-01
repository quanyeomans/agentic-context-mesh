## Enhancements

### dbt platform

- **System logs now surface warnings and errors**: Run step structured logs now show an indicator when system warnings or errors are present, making issues easier to spot during run triage.

- **Region labels now use backend display names**: Account Settings now shows the backend-provided region display name for clearer, more accurate region labeling.

- **SCIM create group UI change**: Changes to our UI to improve the experience of managing groups with SCIM enabled.
 
- **Updated the post-invite message for SSO accounts**:  After a user accepts an invite, the UI now explains that they must log in using SSO to fully redeem the invite and access the account. This replaces the previous "Joined successfully" message and helps avoid confusion when users accept an invite but do not complete the SSO login flow.

### Studio IDE and Copilot

- **Improved crash recovery and not-found routing**: Studio IDE now catches unexpected render failures with a top-level error boundary and shows Not Found more reliably for unknown in-project routes.

- **Improved navigation accessibility and semantics in Studio IDE**: The main navigation trigger area is now a navigation element with improved focus and labeling.

- **Reduced shortcut conflicts with VS Code search**: When Visual Studio Code (VS Code) search is enabled, Studio IDE avoids unregistering Quick Open and suppresses conflicting command palette shortcuts.

### Catalog and Insights Data

- **More accurate source freshness outdated status in Catalog**: Source freshness Outdated status can now be computed at query time, improving freshness status filtering consistency.

- **Improved search and lineage usability in Catalog**: Search results better support column-level navigation and very long queries show a clear validation error, and lineage visuals have improved alignment and reduced edge clutter.

- **Improved cross-project lineage and function awareness in Catalog**: Lineage graph building now includes cross-project dependencies and supports function nodes as first-class lineage entities.

### APIs, Identity, and Administration

- **Project deletion now supported in Admin v2 and v3 Projects APIs**: Projects APIs now explicitly support DELETE with stricter permission checks.