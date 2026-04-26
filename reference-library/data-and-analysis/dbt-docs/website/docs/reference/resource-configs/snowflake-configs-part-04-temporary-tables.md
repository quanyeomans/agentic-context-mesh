## Temporary tables

Incremental table merges for Snowflake prefer to utilize a `view` rather than a `temporary table`. The reasoning is to avoid the database write step that a temporary table would initiate and save compile time. 

However, some situations remain where a temporary table would achieve results faster or more safely. The `tmp_relation_type` configuration enables you to opt in to temporary tables for incremental builds. This is defined as part of the model configuration. 

To guarantee accuracy, an incremental model using the `delete+insert` strategy with a `unique_key` defined requires a temporary table; trying to change this to a view will result in an error.

Defined in the project YAML:

<File name='dbt_project.yml'>

```yaml
name: my_project

...

models:
  <resource-path>:
    +tmp_relation_type: table | view ## If not defined, view is the default.
  
```

</File>

In the configuration format for the model SQL file:

<File name='dbt_model.sql'>

```yaml

{{ config(
    tmp_relation_type="table | view", ## If not defined, view is the default.
) }}

```

</File>