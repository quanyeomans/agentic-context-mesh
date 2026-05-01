## Enhancements

### Orchestration and Run Status

- **Clearer SAO description**: Job settings now describe state-aware orchestration (SAO) as only building models when data or code changes are detected.
- **Direct links for cost optimization setup**: Fusion cost optimization settings now link to account-level Cost Insights settings and setup documentation so you can validate cost data and savings.

### APIs, Identity, and Administration

- **Confirmation when enabling manual SCIM updates**: When you enable manual updates for System for Cross-domain Identity Management (SCIM), dbt platform now asks you to confirm so you do not accidentally allow changes outside your identity provider.
- **More reliable SCIM group provisioning**: SCIM has been updated so that when a SCIM-provisioned user with an expired invite is added to a SCIM-managed group through a SCIM request, the invite is automatically resent during group assignment. This helps prevent errors caused by unaccepted invites.

### dbt platform

- **Project names and descriptions handle empty values better**: Projects with missing names now show as “Untitled Project,” and you can save project descriptions as empty.

### Studio IDE

- **Removed non-functional “Open Settings” actions**: Studio IDE no longer shows “Open Settings” buttons in editor notifications because Studio IDE does not expose VS Code settings, and the action would not help you resolve issues.