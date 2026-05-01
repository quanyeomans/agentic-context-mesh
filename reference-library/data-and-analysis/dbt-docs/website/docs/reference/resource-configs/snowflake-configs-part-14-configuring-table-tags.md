## Configuring table tags 

To add tags to tables, views, and dynamic tables, use the `table_tag` config. Note, the tag must already exist in Snowflake before you apply it.

<File name='models/<modelname>.sql'>

```sql
{{ config(
    table_tag = "my_tag_name = 'my_tag_value'"
) }}

select ...

```

</File>

</VersionBlock>