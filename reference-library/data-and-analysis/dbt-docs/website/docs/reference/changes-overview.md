---
title: "Deprecation & warnings overview"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

When using dbt, you may see warnings or other changes that need your attention. These changes help us move forward with the latest version of dbt and improve the experience for all users. 

Use this page to understand the different types of changes, what to do, and where to find more information.


<Card
  title="Deprecations"
  body="Features in your project code (models, YAML, macros) that still work but will be removed.Impact: Currently warnings; will cause errors in future versions.Action: Update your project code to use the new syntax."
  link="/reference/deprecations"
  icon="dbt-bit"
/>

<Card
  title="Behavior change flags"
  body="Settings in your dbt_project.yml file that let you opt in or out of new behaviors during migration periods.Impact: Controls whether dbt uses old or new behavior; defaults change over time.Action: Set flags to control timing of adoption."
  link="/reference/global-configs/behavior-changes"
  icon="dbt-bit"
/>

<Card
  title="Deprecated CLI flags"
  body="Command-line flags passed to dbt commands that are being removed in Fusion.Impact: Some ignored (with warnings); --models flag will error in Fusion.Action: Update job definitions and scripts to remove or replace these flags."
  link="/docs/dbt-versions/core-upgrade/upgrading-to-fusion#deprecated-flags"
  icon="square-terminal"
/>


## Preparing for Fusion

If you're upgrading to <Constant name="fusion" />, you should:

- [ ] Resolve all [deprecations](/reference/deprecations) to avoid causing errors in <Constant name="fusion" />.
- [ ] Review [behavior change flags](/reference/global-configs/behavior-changes) to understand how <Constant name="fusion" /> will behave (new behavior is always enabled).
- [ ] Update [deprecated CLI flags](/docs/dbt-versions/core-upgrade/upgrading-to-fusion#deprecated-flags) to avoid errors in <Constant name="fusion" />.

## Related docs

- [Full deprecations list](/reference/deprecations)
- [Behavior change flags](/reference/global-configs/behavior-changes)
- [Upgrading to <Constant name="fusion" />](/docs/dbt-versions/core-upgrade/upgrading-to-fusion)
- [<Constant name="fusion" /> readiness checklist](/docs/fusion/fusion-readiness)
- [Events and logging](/reference/events-logging)
