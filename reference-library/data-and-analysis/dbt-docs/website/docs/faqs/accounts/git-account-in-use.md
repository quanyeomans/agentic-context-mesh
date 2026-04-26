---
title: "Why am I getting an \"account in use\" error?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

If you're receiving an 'Account in use' error when trying to integrate GitHub in your Profile page, this is because the <Constant name="git" /> integration is a 1-to-1 integration, so you can only have your <Constant name="git" /> account linked to one <Constant name="dbt" /> user account. 

Here are some steps to take to get you unstuck:

* Log in to the <Constant name="dbt" /> account integrated with your <Constant name="git" /> account. Go to your user profile and click on Integrations to remove the link.

If you don't remember which <Constant name="dbt" /> account is integrated, please email dbt Support at support@getdbt.com and we'll do our best to disassociate the integration for you.
