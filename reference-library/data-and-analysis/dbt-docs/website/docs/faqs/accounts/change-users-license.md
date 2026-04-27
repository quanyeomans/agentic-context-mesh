---
title: "How do I change a user license type to read-only in dbt?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

To change the license type for a user from `developer` to `read-only` or `IT` in <Constant name="dbt" />, you must be an account owner or have admin privileges. You might make this change to free up a billable seat but retain the user’s access to view the information in the <Constant name="dbt" /> account.

1. From <Constant name="dbt" />, click on your account name in the left side menu and, select **Account settings**.

<Lightbox src="/img/docs/dbt-cloud/Navigate-to-account-settings.png" title="Navigate to account settings" />

2. In **Account Settings**, select **Users** under **Teams**.
3. Select the user you want to remove and click **Edit** in the bottom of their profile.
4. For the **License** option, choose **Read-only** or **IT** (from **Developer**), and click **Save**.

<Lightbox src="/img/docs/dbt-cloud/change_user_to_read_only_20221023.gif" title="Change user's license type" />

import LicenseOverrideNote from '/snippets/_license-override-note.md';

<LicenseOverrideNote />
