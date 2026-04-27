---
title: "Fusion Availability"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

:::info Not sure where to start?
Try out the [<Constant name="fusion" /> quickstart](/guides/fusion) and check out the [<Constant name="fusion" /> migration guide](/docs/dbt-versions/core-upgrade/upgrading-to-fusion) to see how to migrate your project.
:::

<Constant name="fusion_engine" /> powers dbt development everywhere — in the [<Constant name="dbt_platform" />](/docs/dbt-versions/upgrade-dbt-version-in-cloud#dbt-fusion-engine), [VS Code/Cursor/Windsurf](/docs/about-dbt-extension), and [locally](/docs/local/install-dbt?version=2#installation). <Constant name="fusion"/> in the <Constant name="dbt_platform"/> is available in private preview. Contact your account team for access.

[<Constant name="dbt_platform" />](/docs/introduction#the-dbt-platform-formerly-dbt-cloud) supports two engines: <Constant name="fusion" /> (Rust-based, fast, visual) and <Constant name="core" /> (Python-based, traditional). <Constant name="core" /> is also available as an [open-source CLI](/docs/introduction#dbt-core) for self-hosted workflows.

Features vary depending on how <Constant name="fusion" /> is implemented. Whether you’re new to dbt or already set up, check out the following table to see what developement solutions are available and where you can use them. See [<Constant name="dbt_platform"/> features](/docs/cloud/about-cloud/dbt-cloud-features) for a full list of the available features for <Constant name="dbt_platform"/>.


|    | Features you can use | Who can use it?  | Solutions available |
| --- | --- | --- | --- |
| **<Constant name="dbt_platform" />** <small>with <Constant name="fusion" /> or <Constant name="core" /> engine </small> | - [<Constant name="canvas" />](/docs/cloud/canvas)- [<Constant name="insights" />](/docs/explore/navigate-dbt-insights#lsp-features) - [<Constant name="studio_ide" />](/docs/cloud/studio-ide/develop-in-studio)- [<Constant name="platform_cli" />](/docs/cloud/cloud-cli-installation)- [dbt VS Code extension](https://marketplace.visualstudio.com/items?itemName=dbtLabsInc.dbt)<small>(VS Code/ Cursor/ Windsurf. Fusion only.)</small>| - <Constant name="dbt_platform" /> licensed users - Anyone getting started with dbt   | - **<Constant name="fusion_engine" />**: Rust-based engine that delivers fast, reliable compilation, analysis, validation, state awareness, and job execution with [visual LSP features](/docs/fusion/supported-features#features-and-capabilities) like autocomplete, inline errors, live previews, and lineage, and more.- **<Constant name="core" />**:  Uses the Python-based <Constant name="core" /> engine for traditional workflows. _Does not_ include <Term id="lsp"/> features.  |
| **Self-hosted Fusion** | - [dbt VS Code extension](https://marketplace.visualstudio.com/items?itemName=dbtLabsInc.dbt)<small>(VS Code/Cursor/Windsurf)</small> - [Fusion CLI](/docs/local/install-dbt?version=2#get-started) | - <Constant name="dbt_platform" /> users- dbt <Constant name="fusion"/> users- Anyone getting started with dbt | - **VS Code extension:** Combines <Constant name="fusion_engine"/> performance with visual <Term id="lsp"/> features when developing locally.- **Fusion CLI:** Provides <Constant name="fusion"/> performance benefits (faster parsing, compilation, execution) but _does not_ include <Term id="lsp"/> features. |
| **Self-hosted dbt Core** | - [dbt Core CLI](/docs/local/install-dbt) | - <Constant name="core" /> users - Anyone getting started with dbt   | Uses the Python-based <Constant name="core" /> engine for traditional workflows. _Does not_ include <Term id="lsp"/> features.  To use the <Constant name="fusion"/> features locally, install [the VS Code extension](/docs/local/install-dbt?version=2#get-started) or [Fusion CLI](/docs/local/install-dbt?version=2#get-started). |
