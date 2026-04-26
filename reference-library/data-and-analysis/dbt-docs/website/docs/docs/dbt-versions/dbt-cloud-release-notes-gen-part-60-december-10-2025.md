## December 10, 2025

### Enhancements

- **AI codegen API**: Streaming middleware enforces request-scoped instrumentation across every AI endpoint, offload warehouse calls via threads, and expose human-readable tool names while gating keyword search behind feature flag for approved tenants.
  
- **dbt platform**
  - **Operations clarity**: Environment profile drawers link directly to connection settings and treat Snowflake fields as optional, while Compare Changes and run-step drawers now explain whether steps failed or were skipped so troubleshooting is faster.
  - **Collaboration & notifications**: Slack Copilot mentions are now more reliable, with hardened workers, support for CSV attachments, and improved logging. Webhook channels now accept longer URLs, handle “warning-only” subscriptions correctly, and automatically clean up corrupted job IDs.
  - **Profile & credential management**: Environment APIs accept `secondary_profile_ids`, run acquisition favors profile-backed credentials, and whoami/auth metrics are scrubbed so cross-platform profiles stay in sync.

- **dbt CLI server**: Improved stability and performance for large projects.
- **Studio IDE**: For dbt Fusion logging, node start and end times will now properly be displayed in command output. 
- **Studio IDE**: Copilot Chat automatically appears anywhere AI entitlements exist, preview runs auto-cancel when nodes change, and keyboard shortcuts respect native keymaps with clear UI labels. 
- **Studio IDE**: Tab view, console pane, and command drawer have been redesigned to enhance efficiency and multitasking.

### Fixes

- **Studio IDE server**: Branch creation now returns explicit feedback for bad branch names/SHAs and detects unauthorized Git errors earlier, making automation failures actionable.