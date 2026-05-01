## Setting row access policies 

Configure [row access policies](https://docs.snowflake.com/en/user-guide/security-row-intro) on tables, views, and dynamic tables by using the `row_access_policy` config for models. The policy must already exist in Snowflake before you apply it to the model.

<File name='models/<modelname>.sql'>

```sql
{{ config(
    row_access_policy = 'my_database.my_schema.my_row_access_policy_name on (id)'
) }}

select ...

```
</File>