---
title: "Record timing info"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The `-r` or `--record-timing-info` flag saves performance profiling information to a file. This file can be visualized with `snakeviz` to understand the performance characteristics of a dbt invocation.

<File name='Usage'>

```text
$ dbt run -r timing.txt
...

$ snakeviz timing.txt
```

</File>

Alternatively, you can use [`py-spy`](https://github.com/benfred/py-spy) to collect [speedscope](https://github.com/jlfwong/speedscope) profiles of dbt commands like this:

```shell
python -m pip install py-spy
sudo py-spy record -s -f speedscope -- dbt parse
```
