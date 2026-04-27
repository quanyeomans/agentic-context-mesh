# override the Snowflake virtual warehouse for just this model
{{
  config(
    materialized='table',
    snowflake_warehouse='EXTRA_LARGE'
  )
}}

with

aggregated_page_events as (

    select
        session_id,
        min(event_time) as session_start,
        max(event_time) as session_end,
        count(*) as count_page_views
    from {{ source('snowplow', 'event') }}
    group by 1

),

index_sessions as (

    select
        *,
        row_number() over (
            partition by session_id
            order by session_start
        ) as page_view_in_session_index
    from aggregated_page_events

)

select * from index_sessions
```

</File>
</TabItem>
</Tabs>