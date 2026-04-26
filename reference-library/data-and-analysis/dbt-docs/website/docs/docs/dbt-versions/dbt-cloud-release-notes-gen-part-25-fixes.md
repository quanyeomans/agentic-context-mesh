## Fixes

### APIs, Identity, and Administration

- **Fewer transient Cloud Config failures:** Retries now only apply to transient errors during Cloud Config lookups, so you should see fewer intermittent failures without added delay for permission, authentication, or not-found responses.

- **More reliable sign in redirects from `current_email`:** If you are already authenticated and land on `/login` with `current_email`, dbt platform now redirects you to `/api/auth/auth-login/` so the email is forwarded during sign in.

- **IP restrictions toggle saves reliably:** Turning IP restrictions on or off now updates form state correctly, so your changes save as expected.

- **More consistent audit log date filtering:** The audit log date range defaults no longer shift during re-renders, so your filters stay stable while you review results.

- **More reliable Single Sign-On (SSO) migration domain updates:** Domain updates during Single Sign-On (SSO) migration no longer rely on mutating existing provider data, which improves save reliability.

### Deployment and Configuration

- **Clearer Bring Your Own Key (BYOK) credential errors:** If your OpenAI credentials include invalid characters, you now get a clearer error message so you can correct the configuration.

- **More reliable credential edits:** Encrypted credential fields now stay optional when you edit credentials, which reduces unexpected validation failures.

- **Correct connection details while editing environments:** You now see the correct connection details more consistently when you edit an environment that uses global connections and connection profiles.

### Orchestration and Run Status

- **Run steps are available for ingestion runs:** You can now open and review run steps for ingestion-triggered runs.

- **Cleaner run error fields:** Run results no longer populate an error string with `None` when dbt does not provide a message or failure count, so you see clearer run error details.

- **Clearer errors for invalid dbt projects:** When Orchestration cannot restore the repository cache because the dbt project is missing or malformed, it now returns an invalid project error so you get a more actionable message in run results.

### Catalog

- **Skipped snapshots show as skipped:** Snapshots selected but not executed in multi-step runs now appear with a skipped status instead of missing run status fields.

### Insights

- **Copilot chat no longer gets stuck loading:** Insights now clears the Copilot chat loading state reliably after responses complete or error, so you can keep chatting without refreshing the page.

- **More reliable Copilot handoff starts:** When you arrive in Insights with a Copilot handoff message, Insights now starts the handoff once and clears stale handoff state when you navigate directly.

### Integrations

- **More consistent JDBC typing for Tableau and Power BI:** Semantic Layer now derives explicit string conversions from returned result metadata, so categorical dimensions and entities are more consistently typed as strings in Tableau and Power BI queries.

### Semantic Layer

- **More reliable cache key deletion:** Cache invalidation no longer fails when an in-memory cache key is already missing, which reduces intermittent errors during cache cleanup.

- **More accurate run ID validation:** Semantic Layer now requests and caches run details scoped to your account, which reduces incorrect run validation results.

### dbt Copilot and agents

- **More reliable cancellations during tool use:** If you cancel a request while an agent is running tools, the agent now recovers cleanly instead of getting stuck on incomplete tool-call history.

- **Cleaner AI diff overlays:** Studio IDE now removes the accept and reject overlay when you leave an artificial intelligence (AI) diff view to prevent stale UI controls.