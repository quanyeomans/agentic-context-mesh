---
title: "SQL DISTINCT"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<head>
    <title>Working with the SQL DISTINCT</title>
</head>

Let’s just put it out there: at one point in your data work, you’ll encounter duplicates in your data. They may be introduced from a faulty data source or created during the joining and transforming of data. You may need a more sophisticated or refactored solution for the latter scenario, but it never hurts to know how to use DISTINCT in a query.

Using DISTINCT in a SELECT statement will force a query to only return non-duplicate rows. You may commonly see a DISTINCT clause in COUNT functions to get counts of distinct rows.

## How to use SQL DISTINCT in a query

To remove duplicate rows from a query, you add DISTINCT immediately after SELECT followed by the rows you want to be selected:

```sql
select
	distinct
	row_1,
	row_2
from my_data_source
```

Let’s take a look at a practical example using DISTINCT below.

### SQL DISTINCT example

```sql
select
	count(customer_id) as cnt_all_orders,
	count(distinct customer_id) as cnt_distinct_customers
from {{ ref('orders') }}
```

This simple query is something you may do while doing initial exploration of your data; it will return the count of `customer_ids` and count of distinct `customer_ids` that appear in the [Jaffle Shop’s](https://github.com/dbt-labs/jaffle_shop) `orders` table:

| cnt_all_orders | cnt_distinct_customers |
|:---:|:---:|
| 99 | 62 |

As you can see from the query results, there are 99 orders placed by customers, but only 62 distinct customers in the table.

## DISTINCT syntax in Snowflake, Databricks, BigQuery, and Redshift

Since it’s a pillar of SQL, all modern data warehouses support the ability to use DISTINCT in a SELECT statement 😀

## DISTINCT use cases

You’ll most commonly see queries using a DISTINCT statement to:

- Remove unnecessary duplicate rows from a data model; a word of caution on this: if you need to use DISTINCT in a downstream, non-source model that contains joins, there’s a chance that there could be faulty logic producing duplicates in the data, so always double-check that they are true duplicates.

- Find the counts of distinct fields in a dataset, especially for primary or surrogate keys.

This isn’t an extensive list of where your team may be using DISTINCT throughout your development work, dbt models, and BI tool logic, but it contains some common scenarios analytics engineers face day-to-day.
