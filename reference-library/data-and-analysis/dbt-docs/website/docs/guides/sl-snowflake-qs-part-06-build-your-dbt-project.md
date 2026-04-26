## Build your dbt project
The next step is to build your project. This involves adding sources, staging models, business-defined entities, and packages to your project.

### Add sources

[Sources](/docs/build/sources) in dbt are the raw data tables you'll transform. By organizing your source definitions, you document the origin of your data. It also makes your project and transformation more reliable, structured, and understandable.

You have two options for working with files in the <Constant name="studio_ide" />:

- **Create a new branch (recommended)** &mdash; Create a new branch to edit and commit your changes. Navigate to **Version Control** on the left sidebar and click **Create branch**.
- **Edit in the protected primary branch** &mdash; If you prefer to edit, format, or lint files and execute dbt commands directly in your primary git branch, use this option. The <Constant name="studio_ide" /> prevents commits to the protected branch so you'll be prompted to commit your changes to a new branch.

Name the new branch `build-project`.

1. Hover over the `models` directory and click the three-dot menu (**...**), then select **Create file**.
2. Name the file `staging/jaffle_shop/src_jaffle_shop.yml` , then click **Create**.
3. Copy the following text into the file and click **Save**.

<File name='models/staging/jaffle_shop/src_jaffle_shop.yml'>

```yaml

sources:
 - name: jaffle_shop
   database: raw
   schema: jaffle_shop
   tables:
     - name: customers
     - name: orders
```

</File>

:::tip
In your source file, you can also use the **Generate model** button to create a new model file for each source. This creates a new file in the `models` directory with the given source name and fill in the SQL code of the source definition.
:::

4. Hover over the `models` directory and click the three dot menu (**...**), then select **Create file**.
5. Name the file `staging/stripe/src_stripe.yml` , then click **Create**.
6. Copy the following text into the file and click **Save**.

<File name='models/staging/stripe/src_stripe.yml'>

```yaml

sources:
 - name: stripe
   database: raw
   schema: stripe
   tables:
     - name: payment
```
</File>

### Add staging models
[Staging models](/best-practices/how-we-structure/2-staging) are the first transformation step in dbt. They clean and prepare your raw data, making it ready for more complex transformations and analyses. Follow these steps to add your staging models to your project.

1. In the `jaffle_shop` sub-directory, create the file `stg_customers.sql`. Or, you can use the **Generate model** button to create a new model file for each source. 
2. Copy the following query into the file and click **Save**.

<File name='models/staging/jaffle_shop/stg_customers.sql'>

```sql
  select
   id as customer_id,
   first_name,
   last_name
  from {{ source('jaffle_shop', 'customers') }}
```

</File>

3. In the same `jaffle_shop` sub-directory, create the file `stg_orders.sql`
4. Copy the following query into the file and click **Save**.

<File name='models/staging/jaffle_shop/stg_orders.sql'>

```sql
  select
    id as order_id,
    user_id as customer_id,
    order_date,
    status
  from {{ source('jaffle_shop', 'orders') }}
```

</File>

5. In the `stripe` sub-directory, create the file `stg_payments.sql`.
6. Copy the following query into the file and click **Save**.

<File name='models/staging/stripe/stg_payments.sql'>

```sql
select
   id as payment_id,
   orderid as order_id,
   paymentmethod as payment_method,
   status,
   -- amount is stored in cents, convert it to dollars
   amount / 100 as amount,
   created as created_at


from {{ source('stripe', 'payment') }}
```

</File>

7. Enter `dbt run` in the command prompt at the bottom of the screen. You should get a successful run and see the three models.

### Add business-defined entities

This phase involves creating [models that serve as the entity layer or concept layer of your dbt project](/best-practices/how-we-structure/4-marts), making the data ready for reporting and analysis.  It also includes adding [packages](/docs/build/packages) and the [MetricFlow time spine](/docs/build/metricflow-time-spine) that extend dbt's functionality.

This phase is the [marts layer](/best-practices/how-we-structure/1-guide-overview#guide-structure-overview), which brings together modular pieces into a wide, rich vision of the entities an organization cares about.

1. Create the file `models/marts/fct_orders.sql`.
2. Copy the following query into the file and click **Save**.

<File name='models/marts/fct_orders.sql'>

```sql
with orders as  (
   select * from {{ ref('stg_orders' )}}
),


payments as (
   select * from {{ ref('stg_payments') }}
),


order_payments as (
   select
       order_id,
       sum(case when status = 'success' then amount end) as amount


   from payments
   group by 1
),


final as (


   select
       orders.order_id,
       orders.customer_id,
       orders.order_date,
       coalesce(order_payments.amount, 0) as amount


   from orders
   left join order_payments using (order_id)
)


select * from final

```

</File>

3. In the `models/marts` directory, create the file `dim_customers.sql`.
4. Copy the following query into the file and click **Save**.

<File name='models/marts/dim_customers.sql'>

```sql
with customers as (
   select * from {{ ref('stg_customers')}}
),
orders as (
   select * from {{ ref('fct_orders')}}
),
customer_orders as (
   select
       customer_id,
       min(order_date) as first_order_date,
       max(order_date) as most_recent_order_date,
       count(order_id) as number_of_orders,
       sum(amount) as lifetime_value
   from orders
   group by 1
),
final as (
   select
       customers.customer_id,
       customers.first_name,
       customers.last_name,
       customer_orders.first_order_date,
       customer_orders.most_recent_order_date,
       coalesce(customer_orders.number_of_orders, 0) as number_of_orders,
       customer_orders.lifetime_value
   from customers
   left join customer_orders using (customer_id)
)
select * from final
```

</File>

5. Create a MetricFlow time spine model by following the [MetricFlow time spine guide](/guides/mf-time-spine?step=1). This guide walks you through creating both the SQL model and YAML configuration required for time-based metric calculations.

6. Enter `dbt run` in the command prompt at the bottom of the screen. You should get a successful run message and also see in the run details that dbt has successfully built your models.