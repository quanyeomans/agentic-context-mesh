## Setting table properties
[Table properties](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-tblproperties.html) can be set with your configuration for tables or views using `tblproperties`:

<File name='with_table_properties.sql'>

```sql
{{ config(
    tblproperties={
      'delta.autoOptimize.optimizeWrite' : 'true',
      'delta.autoOptimize.autoCompact' : 'true'
    }
 ) }}
```

</File>

:::caution

These properties are sent directly to Databricks without validation in dbt, so be thoughtful with how you use this feature.  You will need to do a full refresh of incremental materializations if you change their `tblproperties`.

:::

One application of this feature is making `delta` tables compatible with `iceberg` readers using the [Universal Format](https://docs.databricks.com/en/delta/uniform.html).

```sql
{{ config(
    tblproperties={
      'delta.enableIcebergCompatV2' = 'true'
      'delta.universalFormat.enabledFormats' = 'iceberg'
    }
 ) }}
```

`tblproperties` can be specified for python models, but they will be applied via an `ALTER` statement after table creation.
This is due to a limitation in PySpark.