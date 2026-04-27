## Fixes

### Studio IDE

- **Parse status no longer shows error badge during Fusion compilation**: In Fusion mode, the parse status badge no longer switches to an error state solely because diagnostic errors are present. The badge now correctly reflects compilation progress and completion independent of diagnostic counts.

- **Clearer authentication errors for rejected git connections**: Adds "remote rejected authentication" as a recognized, non-retryable git authentication error. You will now see a clear authentication failure message instead of a misleading retry loop when your git provider rejects your credentials.

### Catalog

- **Reused models no longer flagged as stale**: Models with a `last_run_status` of `reused` are no longer marked stale even when their last execution date exceeds 30 days. This prevents false health issue warnings for models that were intentionally reused rather than re-executed.

- **Resource counts refresh on environment switch**: Fixes a bug where resource counts on the project landing page were not updated when switching environments.