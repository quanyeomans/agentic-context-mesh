---
title: "Unable to trigger a CI job with GitLab"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

When you connect <Constant name="dbt" /> to a GitLab repository, GitLab automatically registers a webhook in the background, viewable under the repository settings. This webhook is also used to trigger [CI jobs](/docs/deploy/ci-jobs) when you push to the repository.

If you're unable to trigger a CI job, this usually indicates that the webhook registration is missing or incorrect.

To resolve this issue, navigate to the repository settings in GitLab and view the webhook registrations by navigating to GitLab --> **Settings** --> **Webhooks**.

Some things to check:

- The webhook registration is enabled in GitLab. 
- The webhook registration is configured with the correct URL and secret.

If you're still experiencing this issue, reach out to the Support team at support@getdbt.com and we'll be happy to help!
