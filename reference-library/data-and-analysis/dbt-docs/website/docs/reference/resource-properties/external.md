---
title: "Definition"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<File name='models/<filename>.yml'>

```yml

sources:
  - name: <source_name>
    tables:
      - name: <table_name>
        external:
          location: <string>
          file_format: <string>
          row_format: <string>
          tbl_properties: <string>      
          partitions:
            - name: <column_name>
              data_type: <string>
              description: <string>
              config:
                meta: {dictionary} # changed to config in v1.10
            - ...
          <additional_property>: <additional_value>
```

</File>

## Definition

An extensible dictionary of metadata properties specific to sources that point to external tables.
There are optional built-in properties, with simple type validation, that roughly correspond to 
the Hive external <Term id="table" /> spec. You may define and use as many additional properties as you'd like.

You may wish to define the `external` property in order to:
- Power macros that introspect [`graph.sources`](/reference/dbt-jinja-functions/graph)
- Define metadata that you can later extract from the [manifest](/reference/artifacts/manifest-json)

For an example of how this property can be used to power custom workflows, see the [`dbt-external-tables`](https://github.com/dbt-labs/dbt-external-tables) package.
