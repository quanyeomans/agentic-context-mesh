## Configuring virtual warehouses

The default warehouse that dbt uses can be configured in your [Profile](/docs/local/profiles.yml) for Snowflake connections. To override the warehouse that is used for specific models (or groups of models), use the `snowflake_warehouse` model configuration. This configuration can be used to specify a larger warehouse for certain models in order to control Snowflake costs and project build times.

[Tests](/docs/build/data-tests) also supports the `snowflake_warehouse` configuration. This can be useful when you want to you run tests on a different Snowflake virtual warehouse than the one used to build models, for example, using a smaller warehouse for lightweight data tests while models run on a larger warehouse.

<Tabs
  defaultValue="dbt_project.yml"
  values={[
    { label: 'Project file', value: 'dbt_project.yml', },
    { label: 'Property file', value: 'models/my_model.yml', },
    { label: 'SQL file config', value: 'models/events/sessions.sql', },
    ]}
>

<TabItem value="dbt_project.yml">

The following example changes the warehouse for a group of models with a config argument in the YAML.

<File name='dbt_project.yml'>

```yaml
name: my_project
version: 1.0.0

...

models:
  +snowflake_warehouse: "EXTRA_SMALL"    # default Snowflake virtual warehouse for all models in the project.
  my_project:
    clickstream:
      +snowflake_warehouse: "EXTRA_LARGE"    # override the default Snowflake virtual warehouse for all models under the `clickstream` directory.
snapshots:
  +snowflake_warehouse: "EXTRA_LARGE"    # all Snapshot models are configured to use the `EXTRA_LARGE` warehouse.
data_tests:
  +snowflake_warehouse: "EXTRA_SMALL"    # all data tests are configured to use the `EXTRA_SMALL` warehouse.
```

</File>
</TabItem>
<TabItem value="models/my_model.yml">

The following example overrides the Snowflake warehouse for a single model and a specific test using a config argument in the property file.

<File name='models/my_model.yml'>

```yaml
models:
  - name: my_model
    config:
      snowflake_warehouse: "EXTRA_LARGE"    # override the Snowflake virtual warehouse just for this model
    columns:
      - name: id
        data_tests:
          - unique:
              config:
                snowflake_warehouse: "EXTRA_SMALL"    # use a smaller warehouse for this test
```

</File>
</TabItem>
<TabItem value="models/events/sessions.sql">

The following example changes the warehouse for a single model with a config() block in the SQL model.

<File name='models/events/sessions.sql'>

```sql