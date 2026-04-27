---
title: "Does my `.yml` file containing tests and descriptions need to be named `schema.yml`?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

No! You can name this file whatever you want (including `whatever_you_want.yml`), so long as:
* The file is in your `models/` directory¹
* The file has `.yml` extension

Check out the [docs](/reference/configs-and-properties) for more information.

¹If you're declaring properties for seeds, snapshots, or macros, you can also place this file in the related directory — `seeds/`, `snapshots/` and `macros/` respectively.
