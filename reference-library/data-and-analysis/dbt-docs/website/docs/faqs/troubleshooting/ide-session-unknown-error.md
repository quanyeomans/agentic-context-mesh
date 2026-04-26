---
title: "I'm receiving an 'Your IDE session experienced an unknown error and was terminated. Please contact support'."
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

If you're seeing the following error when you launch the <Constant name="studio_ide" />, it could be due to a few scenarios but, commonly, it indicates a missing repository:

```shell

Your <Constant name="studio_ide" /> session experienced an unknown error and was terminated. Please contact support.

```

You can try to resolve this by adding a repository like a [managed repository](/docs/cloud/git/managed-repository) or your preferred <Constant name="git" /> account. To add your <Constant name="git" /> account, navigate to **Project** > **Repository** and select your repository.


If you're still running into this error, please contact the Support team at support@getdbt.com for help.
