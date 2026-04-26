## Enhancements

### Studio IDE

- **More reliable dark mode on initial load**: Added additional layers of theme preference fallbacks, including the user's OS theme preferences, to avoid incorrect theming when the user-preferences service is slow to respond.

- **Deep-linking to console tabs**: You can now navigate directly to a specific Studio IDE console tab (for example, commands or lineage) using a `consoleTab` URL query parameter. Invalid tab identifiers are removed from the URL automatically.

- **Compile button after deprecation autofix in Fusion**: After the deprecation autofix workflow completes in Fusion environments, a **Compile** button now appears in the autofix results panel so you can immediately verify the updated project without manually triggering a compile.

### Orchestration and run status

- **Fusion eligibility toggle replaces dropdown filter**: The Fusion eligibility dropdown filter on the jobs list has been replaced with a toggle and help icon. When enabled, each job displays its current Fusion eligibility badge, and a persistent info banner explains how eligibility is recalculated. The toggle state is saved per-project in your browser.

- **Debug on Fusion menu**: The single **Run once on Fusion** button on the job details page and job list has been replaced with a **Debug on Fusion** menu that offers **Debug in Studio**, **Run once on Fusion**, and (when dbt Copilot is enabled) **Debug in Studio with Copilot** options. Refer to [Prepare to upgrade to <Constant name="fusion"/>](https://docs.getdbt.com/guides/prepare-fusion-upgrade?step=7) for more info.

- **Simplified Fusion run error banner**: The Fusion run error banner on run details now uses the same **Debug on Fusion** menu as the jobs page. The banner no longer requires setting a personal dbt version override before navigating to Studio.

### Webhooks

- **Webhook test flow uses receipt polling**: Testing a webhook subscription now triggers a test event and polls for the delivery receipt, showing the actual HTTP status code and error from the endpoint response. A 60-second timeout is applied, with a clear timeout message if the endpoint does not respond in time.

- **Webhook receipt endpoint returns 404 for pending events**: The receipt endpoint for webhook events now returns a `404` response when a delivery record has not yet been written (for example, when the notification system has not yet processed the event), rather than returning an incomplete record.

- **Corrected status code for timed-out webhook deliveries**: Webhook delivery history records now show `504` as the HTTP status code when a delivery timed out (previously stored as `0`), improving accuracy in the delivery history view.

- **Webhook event history note always visible**: The note that event history is limited to the past 7 days now appears on the webhook events history page unconditionally.

### Integrations

- **Slack notification settings migration banner**: A migration banner now appears on the Slack notification settings page when you have notification settings from a previous Slack integration. You can migrate them to the new Slack app in one click or dismiss the banner. After migration, you are shown which private channels need the dbt Cloud bot invited for notifications to be delivered. Contact your account manager to enable.

### dbt platform

- **View account information scope on OAuth consent page**: The OAuth consent page now displays a "View account information" (`account:read`) scope option, which grants view-only access to account details including project and environment information.

- **PrivateLink endpoint pending status**: A new `pending` connectivity status is available for PrivateLink endpoints, in addition to the existing `success` and `failed` states.

- **Permission added to member role**: The member permission set now includes `fusion_readiness_read`, allowing members to view Fusion readiness information for projects without requiring elevated permissions.