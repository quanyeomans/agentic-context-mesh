## Materialized views and streaming tables

[Materialized views](https://docs.databricks.com/en/sql/user/materialized-views.html) and [streaming tables](https://docs.databricks.com/en/sql/load-data-streaming-table.html) are alternatives to incremental tables that are powered by [Delta Live Tables](https://docs.databricks.com/en/delta-live-tables/index.html).
See [What are Delta Live Tables?](https://docs.databricks.com/en/delta-live-tables/index.html#what-are-delta-live-tables-datasets) for more information and use cases.

In order to adopt these materialization strategies, you will need a workspace that is enabled for Unity Catalog and serverless SQL Warehouses.

<File name='materialized_view.sql'>

```sql
{{ config(
   materialized = 'materialized_view'
 ) }}
```

</File>

or

<File name='streaming_table.sql'>

```sql
{{ config(
   materialized = 'streaming_table'
 ) }}
```

</File>

We support [on_configuration_change](/reference/resource-configs/on_configuration_change) for most available properties of these materializations.
The following table summarizes our configuration support:

| Databricks Concept | Config Name | MV/ST support | Version |
| ------------------ | ------------| ------------- | ------- |
| [PARTITIONED BY](https://docs.databricks.com/en/sql/language-manual/sql-ref-partition.html#partitioned-by) | `partition_by` | MV/ST | All |
| [CLUSTER BY](https://docs.databricks.com/en/delta/clustering.html) | `liquid_clustered_by` | MV/ST | v1.11+ |
| COMMENT | [`description`](/reference/resource-properties/description) | MV/ST | All |
| [TBLPROPERTIES](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-tblproperties.html#tblproperties) | `tblproperties` | MV/ST | All |
| [TAGS](https://docs.databricks.com/en/data-governance/unity-catalog/tags.html) | `databricks_tags` | MV/ST | v1.11+ |
| [SCHEDULE CRON](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-create-materialized-view.html#parameters) | `schedule: { 'cron': '\<cron schedule\>', 'time_zone_value': '\<time zone value\>' }` | MV/ST | All |
| query | defined by your model SQL | on_configuration_change for MV only | All |

<File name='mv_example.sql'>

```sql

{{ config(
    materialized='materialized_view',
    partition_by='id',
    schedule = {
        'cron': '0 0 * * * ? *',
        'time_zone_value': 'Etc/UTC'
    },
    tblproperties={
        'key': 'value'
    },
) }}
select * from {{ ref('my_seed') }}

```

</File>

### Configuration details

#### partition_by
`partition_by` works the same as for views and tables, i.e. can be a single column, or an array of columns to partition by.

#### liquid_clustered_by
_Available in versions 1.11 or higher_

`liquid_clustered_by` enables [liquid clustering](https://docs.databricks.com/en/delta/clustering.html) for materialized views and streaming tables. Liquid clustering optimizes query performance by co-locating similar data within the same files, particularly beneficial for queries with selective filters on the clustered columns.

**Note:** You cannot use both `partition_by` and `liquid_clustered_by` on the same materialization, as Databricks doesn't allow combining these features.

#### databricks_tags
_Available in versions 1.11 or higher_

`databricks_tags` allows you to apply [Unity Catalog tags](https://docs.databricks.com/en/data-governance/unity-catalog/tags.html) to your materialized views and streaming tables for data governance and organization. Tags are key-value pairs that can be used for data classification, access control policies, and metadata management.

```sql
{{ config(
    materialized='streaming_table',
    databricks_tags={'pii': 'contains_email', 'team': 'analytics'}
) }}
```

Tags are applied via `ALTER` statements after the materialization is created. Once applied, tags cannot be removed through dbt-databricks configuration changes. To remove tags, you must use Databricks directly or a post-hook.

#### description
As with views and tables, adding a `description` to your configuration will lead to a table-level comment getting added to your materialization.

#### tblproperties
`tblproperties` works the same as for views and tables with an important exception: the adapter maintains a list of keys that are set by Databricks when making an materialized view or streaming table which are ignored for the purpose of determining configuration changes.

#### schedule
Use this to set the refresh schedule for the model.  If you use the `schedule` key, a `cron` key is required in the associated dictionary, but `time_zone_value` is optional (see the example above).  The `cron` value should be formatted as documented by Databricks.
If a schedule is set on the materialization in Databricks and your dbt project does not specify a schedule for it (when `on_configuration_change` is set to `apply`), the refresh schedule will be set to manual when you next run the project.
Even when schedules are set, dbt will request that the materialization be refreshed manually when run.

#### query
For materialized views, if the compiled query for the model differs from the query in the database, we will the take the configured `on_configuration_change` action.
Changes to query are not currently detectable for streaming tables; see the next section for details.

### on_configuration_change 
`on_configuration_change` is supported for materialized views and streaming tables, though the two materializations handle it different ways.

#### Materialized Views
Currently, the only change that can be applied without recreating the materialized view in Databricks is to update the schedule.
This is due to limitations in the Databricks SQL API.

#### Streaming Tables
For streaming tables, only changes to the partitioning currently requires the table be dropped and recreated.
For any other supported configuration change, we use `CREATE OR REFRESH` (plus an `ALTER` statement for changes to the schedule) to apply the changes.
There is currently no mechanism for the adapter to detect if the streaming table query has changed, so in this case, regardless of the behavior requested by on_configuration_change, we will use a `create or refresh` statement (assuming `partitioned by` hasn't changed); this will cause the query to be applied to future rows without rerunning on any previously processed rows.
If your source data is still available, running with '--full-refresh' will reprocess the available data with the updated current query.