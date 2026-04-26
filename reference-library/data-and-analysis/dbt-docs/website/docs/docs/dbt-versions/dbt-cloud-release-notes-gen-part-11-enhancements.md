## Enhancements

### dbt Copilot and agents

- **Smarter validation after file edits**: The Studio DevAgent now selects the lightest appropriate validation check after each change — for example, skipping compilation for description-only edits and running `dbt parse` for project config changes — instead of always running a full `dbt compile`. This reduces unnecessary round-trips and keeps iteration faster.

### Studio IDE

- **Deferral environment selector**: Replaces the simple defer-to-production toggle with a popover that lets you choose between your development environment, dbt's default deferral behavior (staging if available, otherwise production), or a specific custom environment. A badge in the command bar shows your current deferral target at a glance.

- **Revert personal dbt version override**: Adds an "Edit / Revert" action to the version override option in the environment popover. Clicking "Revert" opens a confirmation modal that removes your personal dbt version override and restarts the session.


- **Improved file context pill in dbt Copilot**: Moves the active-file context pill to above the text input for greater visibility. When you remove the file context, a "Use current file as context" affordance appears so you can restore it without switching tabs.

### Catalog

- **Reused test status in DAG lens**: State-Aware Orchestration (SAO) test runs that reuse prior results now display with a "reused" icon in the DAG test status lens, matching the existing model run status behavior.


- **Function resource type support in selectors**: The `function` resource type is now recognized in dbt selectors and the resource node type map, enabling correct filtering and navigation for function resources in Catalog.

### Insights

- **Fusion status column in account insights table**: Look for a "Fusion status" column in your account insights table when the Fusion readiness flow is available for your account. You'll see one of four states: On Fusion, Start upgrade, Partial-Fusion, or Non-Fusion — based on each project's readiness and migration progress. Projects that are ready to upgrade show a "Start upgrade" button that navigates directly to the project home page. Contact your account manager to enable.