# Newly added          
metrics: 
  # Simple type metrics
  - name: "order_total"
    description: "Sum of orders value"
    type: simple
    label: "order_total"
    type_params:
      measure:
        name: order_total
  - name: "order_count"
    description: "number of orders"
    type: simple
    label: "order_count"
    type_params:
      measure:
        name: order_count
  - name: large_orders
    description: "Count of orders with order total over 20."
    type: simple
    label: "Large Orders"
    type_params:
      measure:
        name: order_count
    filter: |
      {{ Metric('order_total', group_by=['order_id']) }} >=  20
  # Ratio type metric
  - name: "avg_order_value"
    label: "avg_order_value"
    description: "average value of each order"
    type: ratio
    type_params:
      numerator: 
        name: order_total
      denominator: 
        name: order_count
  # Cumulative type metrics
  - name: "cumulative_order_amount_mtd"
    label: "cumulative_order_amount_mtd"
    description: "The month to date value of all orders"
    type: cumulative
    type_params:
      measure:
        name: order_total
      cumulative_type_params:
        grain_to_date: month
  # Derived metric
  - name: "pct_of_orders_that_are_large"
    label: "pct_of_orders_that_are_large"
    description: "percent of orders that are large"
    type: derived
    type_params:
      expr: large_orders/order_count
      metrics:
        - name: large_orders
        - name: order_count
```

</File>

</VersionBlock>

<VersionBlock firstVersion="1.12">

<File name='models/fct_orders.yml'>

```yaml
models:
  - name: fct_orders
    semantic_model:
      enabled: true
      name: orders
      
    agg_time_dimension: order_date
    
    columns:
      - name: order_id
        entity:
          type: primary
      - name: customer_id
        entity:
          name: customer
          type: foreign
      - name: order_date
        granularity: day
        dimension:
          type: time
      - name: amount
        dimension:
          type: categorical
          
    metrics:
      # Simple type metrics
      - name: order_total
        description: Sum of orders value
        type: simple
        label: Order total
        agg: sum
        expr: amount
      - name: order_count
        description: Number of orders
        type: simple
        label: Order count
        agg: sum
        expr: 1
      - name: customers_with_orders
        description: Distinct count of customers placing orders
        type: simple
        label: Customers with orders
        agg: count_distinct
        expr: customer_id
      - name: large_orders
        description: Count of orders with order total over 20
        type: simple
        label: Large orders
        agg: sum
        expr: 1
        filter: |
          {{ Dimension('order_id__amount') }} >= 20
      # Ratio type metric
      - name: avg_order_value
        label: Average order value
        description: Average value of each order
        type: ratio
        numerator: order_total
        denominator: order_count
      # Cumulative type metrics
      - name: cumulative_order_amount_mtd
        label: Cumulative order amount MTD
        description: The month to date value of all orders
        type: cumulative
        grain_to_date: month
        input_metric: order_total
      # Derived metric
      - name: pct_of_orders_that_are_large
        label: Percent of orders that are large
        description: Percent of orders that are large
        type: derived
        expr: large_orders / order_count
        input_metrics:
          - name: large_orders
          - name: order_count
```

</File>

</VersionBlock>

### Add second semantic model to your project

<VersionBlock lastVersion="1.11">
Great job, you've successfully built your first semantic model! It has all the required elements: entities, dimensions, measures, and metrics.

</VersionBlock>

<VersionBlock firstVersion="1.12">
Great job, you've successfully built your first semantic model! It has all the required elements: entities, dimensions, and metrics.
</VersionBlock>

Let’s expand your project's analytical capabilities by adding another semantic model in your other marts model, such as: `dim_customers.yml`.

After setting up your orders model:

1. Create the file `dim_customers.yml`.
2. Copy the following code into the file and click **Save**.

<VersionBlock lastVersion="1.11">

<File name='models/metrics/dim_customers.yml'>

```yaml
semantic_models:
  - name: customers
    defaults:
      agg_time_dimension: most_recent_order_date
    description: |
      semantic model for dim_customers
    model: ref('dim_customers')
    entities:
      - name: customer
        expr: customer_id
        type: primary
    dimensions:
      - name: customer_name
        type: categorical
        expr: first_name
      - name: first_order_date
        type: time
        type_params:
          time_granularity: day
      - name: most_recent_order_date
        type: time
        type_params:
          time_granularity: day
    measures:
      - name: count_lifetime_orders
        description: Total count of orders per customer.
        agg: sum
        expr: number_of_orders
      - name: lifetime_spend
        agg: sum
        expr: lifetime_value
        description: Gross customer lifetime spend inclusive of taxes.
      - name: customers
        expr: customer_id
        agg: count_distinct

metrics:
  - name: "customers_with_orders"
    label: "customers_with_orders"
    description: "Unique count of customers placing orders"
    type: simple
    type_params:
      measure:
        name: customers
```

</File>

</VersionBlock>

<VersionBlock firstVersion="1.12">

<File name='models/dim_customers.yml'>

```yaml
models:
  - name: dim_customers
    semantic_model:
      enabled: true
      name: customers
      
    agg_time_dimension: most_recent_order_date
    
    columns:
      # Entities
      - name: customer_id
        entity:
          name: customer
          type: primary
      # Dimensions
      - name: first_name
        dimension:
          name: customer_name
          type: categorical
      - name: first_order_date
        granularity: day
        dimension:
          type: time
      - name: most_recent_order_date
        granularity: day
        dimension:
          type: time
      - name: number_of_orders
        dimension:
          type: categorical
      - name: lifetime_value
        dimension:
          type: categorical
          
    # Metrics
    metrics:
      - name: customers_with_orders
        label: Customers with orders
        description: Unique count of customers placing orders
        type: simple
        agg: count_distinct
        expr: customer_id
      - name: count_lifetime_orders
        description: Total count of orders per customer
        type: simple
        label: Lifetime orders
        agg: sum
        expr: number_of_orders
      - name: lifetime_spend
        description: Gross customer lifetime spend inclusive of taxes
        type: simple
        label: Lifetime spend
        agg: sum
        expr: lifetime_value
```

</File>

</VersionBlock>

This semantic model uses simple metrics to focus on customer metrics and emphasizes customer dimensions like name, type, and order dates. It uniquely analyzes customer behavior, lifetime value, and order patterns.