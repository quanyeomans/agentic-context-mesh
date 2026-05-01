## Enhancements

### Studio IDE

- **Faster file search:** Studio IDE now reuses its file-search index across searches, so repeated searches return results faster.

- **More responsive Git status decorations:** Studio IDE debounces rapid file change events and avoids applying stale responses, so Git status badges update more reliably during bulk edits and saves.

- **Clearer server status details:** The server status popover uses a clearer grouped layout and action buttons to help you troubleshoot development credentials and server health. Please contact your account manager to enable.

### dbt Copilot and agents

- **More accurate product guidance:** Copilot and agents can use a product documentation toolset to answer product and workflow questions more reliably.

- **Full-screen Copilot view:** You can open Copilot in a dedicated full-screen view for a more focused chat and coding workflow. Please contact your account manager to enable.

- **Lighter default file context:** Copilot now references your active file by path instead of automatically attaching the file contents, which reduces message size and improves chat reliability.

- **Run `dbt-autofix` from Copilot and agents:** Copilot can run `dbt-autofix` commands (with confirmation) and stream the output into chat, and Studio IDE agents can run `dbt-autofix` using `run_autofix` for bulk deprecation fixes and migrations.

### Catalog

- **Custom materialization filter:** Catalog search now groups non-standard materializations under a single “Custom” filter, so you can narrow results without picking each materialization type.

### Insights

- **More complete Redshift query attribution:** Insights can resolve missing Redshift query IDs from warehouse query history when artifacts do not include them, improving cost coverage for runs with executions.

- **Copilot entry stays available during lockouts:** If dbt Copilot is temporarily locked for your account, you can still open Copilot from Insights to see lock details.

### Orchestration and Run Status

- **Run metadata includes triggering and canceling actors:** Run details now include who triggered or canceled a run (user or service token), which helps you audit run activity.

- **Custom branch preserved for runs and reruns:** When an environment uses a custom branch, dbt platform now carries that branch through run triggers, retries, and reruns more consistently.

- **Fusion readiness metadata for jobs and environments:** You can now retrieve Fusion readiness signals for projects, environments, and jobs to support Fusion migration planning. Please contact your account manager to enable.

- **More accurate command names for dbt Fusion runs:** Orchestration now reads the invocation name from `run_results.json` using `command` when `invocation_command` is missing, so you see the correct dbt command in run details.

### Run Logs

- **More resilient run step history ingestion:** Run step history ingestion now drops invalid events and de-duplicates redundant step-start events before writing step data, improving step-level accuracy. Please contact your account manager to enable.

### Deployment and Configuration

- **Longer project descriptions:** You can now add project descriptions of up to 1,024 characters.

- **Connection links in profiles:** You can now open a connection directly from the connection profile table in a new tab.

- **Clearer YAML validation for extended attributes:** You now get more consistent validation and clearer error messages for invalid YAML syntax, null values, and non-object YAML content when you edit extended attributes.

### Semantic Layer

- **Improved filtered-query cache matching:** Cached query results can now be matched and reused more reliably when your query includes filters, which can reduce repeated compilation and improve response times.