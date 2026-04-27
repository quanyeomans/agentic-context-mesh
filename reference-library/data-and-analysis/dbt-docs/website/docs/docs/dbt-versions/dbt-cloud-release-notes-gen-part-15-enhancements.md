## Enhancements

### dbt Copilot and agents

- **Persistent agent mode across sessions**: The Studio agent now remembers your last-used mode (Ask or Code) across browser sessions, so you no longer need to reselect it each time you open the IDE.

### Studio IDE

- **More accurate file search results**: File search now validates each result against the filesystem before returning matches. Files that have been deleted locally but not yet staged are no longer included in search results.

- **No unexpected git pulls on the primary branch**: Removes behavior where the IDE server automatically pulled changes from your primary branch during git status checks, which could cause unintended overwrites for projects using trunk-based development.

### Orchestration and run status

- **Teradata column-level lineage support**: Adds Teradata to the SQL dialect adapter map, enabling column-level lineage parsing for dbt projects using the Teradata adapter.

### APIs, Identity, and Administration

- **Fusion status includes readiness and migration availability**: Adds fields indicating availability of readiness and migration features.

- **Faster account feature flag propagation**: Account feature flag changes now take effect within 60 seconds instead of up to one hour. You should see feature toggles apply more promptly across your account.

### Webhooks

- **Notification delivery reliability improvements**: Reduced the likelihood of delayed notifications (webhooks, email, Slack, and Teams) in certain third-party/system disruption scenarios.