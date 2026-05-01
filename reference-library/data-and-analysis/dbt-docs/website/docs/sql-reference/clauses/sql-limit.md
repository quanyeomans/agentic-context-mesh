---
title: "SQL LIMIT"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<head>
    <title>Working with the SQL LIMIT clause</title>
</head>

When you’re developing data models or drafting up a query, do you usually need to see all results from it? Not normally. Hence, we LIMIT.

Adding the LIMIT clause to a query will limit the number of rows returned. It’s useful for when you’re developing data models, ensuring SQL in a query is functioning as expected, and wanting to save some money during development periods.

## How to use the LIMIT clause in a query

To limit the number of rows returned from a query, you would pass the LIMIT in the last line of the query with the number of rows you want returned:

```sql
select
	some_rows
from my_data_source
limit 
```

Let’s take a look at a practical example using LIMIT below.

### LIMIT example

```sql
select
	order_id,
	order_date,
	rank () over (order by order_date) as order_rnk
from {{ ref('orders') }}
order by 2
limit 5
```

This simple query using the [Jaffle Shop’s](https://github.com/dbt-labs/jaffle_shop) `orders` table will return these exact 5 rows:

| order_id | order_date | order_rnk |
|:---:|:---:|:---:|
| 1 | 2018-01-01 | 1 |
| 2 | 2018-01-02 | 2 |
| 3 | 2018-01-04 | 3 |
| 4 | 2018-01-05 | 4 |
| 5 | 2018-01-05 | 4 |

After ensuring that this is the result you want from this query, you can omit the LIMIT in your final data model.

:::tip Save money and time by limiting data in development
You could limit your data used for development by manually adding a LIMIT statement, a WHERE clause to your query, or by using a [dbt macro to automatically limit data based](/best-practices/best-practice-workflows#limit-the-data-processed-when-in-development) on your development environment to help reduce your warehouse usage during dev periods.
:::

## LIMIT syntax in Snowflake, Databricks, BigQuery, and Redshift

All modern data warehouses support the ability to LIMIT a query and the syntax is also the same across them. Use the table below to read more on the documentation for limiting query results in your data warehouse.

| Data warehouse | LIMIT support? |
|:---:|:---:|
| Snowflake | ✅ |
| Databricks | ✅ |
| Amazon Redshift | ✅ |
| Google BigQuery | ✅ |

## LIMIT use cases

We most commonly see queries limited in data work to:
- Save some money in development work, especially for large datasets;  just make sure the model works across a subset of the data instead of all of the data 💸
- Paired with an ORDER BY statement, grab the top 5, 10, 50, 100, etc. entries from a dataset

This isn’t an extensive list of where your team may be using LIMIT throughout your development work, but it contains some common scenarios analytics engineers face day-to-day.
