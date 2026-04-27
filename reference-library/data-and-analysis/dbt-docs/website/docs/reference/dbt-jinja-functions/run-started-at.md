---
title: "About run_started_at variable"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

`run_started_at` outputs the timestamp that this run started, e.g. `2017-04-21 01:23:45.678`.

The `run_started_at` variable is a Python `datetime` object. As of 0.9.1, the timezone of this variable
 defaults to UTC.

<File name='run_started_at_example.sql'>

```sql
select
	'{{ run_started_at.strftime("%Y-%m-%d") }}' as date_day

from ...
```

</File>

To modify the timezone of this variable, use the `pytz` module:

<File name='run_started_at_utc.sql'>

```sql
select
	'{{ run_started_at.astimezone(modules.pytz.timezone("America/New_York")) }}' as run_started_est

from ...
```

</File>
