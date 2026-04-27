## Behavior Changes

### Studio IDE

- **Prevent destructive root operations**: Prevents rename and delete operations on the repository root and shows clearer warnings.

- **Resumable dbt command log streaming**: Improves dbt command log streaming reliability by resuming from the last known Command Line Interface (CLI) event offset. Contact your account manager to enable.

### Admin And APIs

- **Job Admin gains write access in Profiles API**: Job Admin now includes `profiles_write`, which can change what Job Admin users can do where Profiles are enabled.

- **Search parameter renamed**: Version 3 Private Endpoints query parameter `name_search` is renamed to `search`, and search matches endpoint name and endpoint value.

- **Connections: Postgres database name required**: Postgres connection validation now requires a non-empty database name.

- **User credentials: Prevent sharing credentials across users**: Prevents associating the same active credentials object to multiple users, returning a conflict instead of silently duplicating associations.

### Integrations

- **GitHub: More flexible repository URL schemes**: GitHub shared webhooks now accept repository URLs using https, git, and Secure Shell (SSH) formats.

- **Slack: Tighter permission gating for settings**: Slack linking and notification settings are more strictly gated by the relevant permissions.

- **Slack: Permission check aligned to job notification access**: Slack integration listing now uses job notifications read permission, reducing incorrect permission-denied scenarios.

### CLI Runtime

- **Shorter default request timeouts**: Reduces default timeouts from 60 seconds to 5 seconds for Cloud Config and Cloud Artifact calls, causing requests to fail faster in high-latency environments unless overridden.

- **OpenTelemetry logs: Corrected JSON field name**: Corrects the OpenTelemetry (OTel) log payload field name to `additional_message` (from the misspelled `addtional_message`), which may require updates to downstream parsing.