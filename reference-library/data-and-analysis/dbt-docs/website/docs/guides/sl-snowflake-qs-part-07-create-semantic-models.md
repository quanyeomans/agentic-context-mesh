## Create semantic models

In this section, you'll learn about [semantic model](/guides/sl-snowflake-qs?step=6#about-semantic-models), [their components](/guides/sl-snowflake-qs?step=6#semantic-model-components), and [how to configure a time spine](/guides/sl-snowflake-qs?step=6#configure-a-time-spine).

### About semantic models

<VersionBlock lastVersion="1.11">

[Semantic models](/docs/build/semantic-models) contain many object types (such as entities, measures, and dimensions) that allow MetricFlow to construct the queries for metric definitions.

- Each semantic model will be 1:1 with a dbt SQL/Python model.
- Each semantic model will contain (at most) 1 primary or natural entity.
- Each semantic model will contain zero, one, or many foreign or unique entities used to connect to other entities.
- Each semantic model may also contain dimensions, measures, and metrics. This is what actually gets fed into and queried by your downstream BI tool.

In the following steps, semantic models enable you to define how to interpret the data related to orders. It includes entities (like ID columns serving as keys for joining data), dimensions (for grouping or filtering data), and measures (for data aggregations).

1. In the `metrics` sub-directory, create a new file `fct_orders.yml`.

:::tip 
Make sure to save all semantic models and metrics under the directory defined in the [`model-paths`](/reference/project-configs/model-paths) (or a subdirectory of it, like `models/semantic_models/`). If you save them outside of this path, it will result in an empty `semantic_manifest.json` file, and your semantic models or metrics won't be recognized.
:::

2. Add the following code to that newly created file:

<File name='models/metrics/fct_orders.yml'>

```yaml
semantic_models:
  - name: orders
    defaults:
      agg_time_dimension: order_date
    description: |
      Order fact table. This table’s grain is one row per order.
    model: ref('fct_orders')
```

</File>

</VersionBlock>

<VersionBlock firstVersion="1.12">

[Semantic models](/docs/build/semantic-models) contain many object types (such as entities, simple metrics, and dimensions) that allow MetricFlow to construct the queries for metric definitions.

- Each semantic model will be 1:1 with a dbt SQL model.
- Each semantic model will contain (at most) 1 primary or natural entity.
- Each semantic model will contain zero, one, or many foreign or unique entities used to connect to other entities.
- Each semantic model may also contain dimensions, simple metrics, and metrics. This is what actually gets fed into and queried by your downstream BI tool.

In the following steps, semantic models enable you to define how to interpret the data related to orders. It includes entities (like ID columns serving as keys for joining data), dimensions (for grouping or filtering data), and simple metrics (for data aggregations).

1. In the `models` directory, create a new file `fct_orders.yml`.

:::tip 
Make sure to save all semantic models and metrics under the directory defined in the [`model-paths`](/reference/project-configs/model-paths) (or a subdirectory of it, like `models/semantic_models/`). If you save them outside of this path, it will result in an empty `semantic_manifest.json` file, and your semantic models or metrics won't be recognized.
:::

2. Add the following code to that newly created file:

<File name='models/fct_orders.yml'>

```yaml
models:
  - name: fct_orders
    description: |
      Order fact table. This table's grain is one row per order.
    semantic_model:
      enabled: true
      name: orders
      
    agg_time_dimension: order_date
```

</File>

</VersionBlock>

### Semantic model components

<VersionBlock lastVersion="1.11">

The following sections explain [dimensions](/docs/build/dimensions), [entities](/docs/build/entities), and [measures](/docs/build/measures) in more detail, showing how they each play a role in semantic models.

- [Entities](#entities) act as unique identifiers (like ID columns) that link data together from different tables.
- [Dimensions](#dimensions) categorize and filter data, making it easier to organize.
- [Measures](#measures) calculates data, providing valuable insights through aggregation.

</VersionBlock>

<VersionBlock firstVersion="1.12">

The following sections explain [dimensions](/docs/build/dimensions), [entities](/docs/build/entities), and [simple metrics](/docs/build/metrics-overview#simple-metrics) in more detail, showing how they each play a role in semantic models.

- [Entities](#entities) act as unique identifiers (like ID columns) that link data together from different tables.
- [Dimensions](#dimensions) categorize and filter data, making it easier to organize.
- [Simple metrics](#simple-metrics) calculates data using an aggregation function, providing valuable insights through aggregation.

</VersionBlock>

### Entities

[Entities](/docs/build/semantic-models#entities) are a real-world concept in a business, serving as the backbone of your semantic model. These are going to be ID columns (like `order_id`) in our semantic models. These will serve as join keys to other semantic models.

Add entities to your `fct_orders.yml` semantic model file:

<VersionBlock lastVersion="1.11">

<File name='models/metrics/fct_orders.yml'>

```yaml
semantic_models:
  - name: orders
    defaults:
      agg_time_dimension: order_date
    description: |
      Order fact table. This table’s grain is one row per order.
    model: ref('fct_orders')
    # Newly added
    entities: 
      - name: order_id
        type: primary
      - name: customer
        expr: customer_id
        type: foreign
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
      # Entities
      - name: order_id
        entity:
          type: primary
      - name: customer_id
        entity:
          name: customer
          type: foreign
```

</File>

</VersionBlock>

### Dimensions

[Dimensions](/docs/build/semantic-models#dimensions) are a way to group or filter information based on categories or time. 

Add dimensions to your `fct_orders.yml` semantic model file:

<VersionBlock lastVersion="1.11">
<File name='models/metrics/fct_orders.yml'>

```yaml
semantic_models:
  - name: orders
    defaults:
      agg_time_dimension: order_date
    description: |
      Order fact table. This table’s grain is one row per order.
    model: ref('fct_orders')
    entities:
      - name: order_id
        type: primary
      - name: customer
        expr: customer_id
        type: foreign
    # Newly added
    dimensions:   
      - name: order_date
        type: time
        type_params:
          time_granularity: day
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
      # Entities
      - name: order_id
        entity:
          type: primary
      - name: customer_id
        entity:
          name: customer
          type: foreign
      # Newly added - Dimensions      
      - name: order_date
        granularity: day
        dimension:
          type: time
```

</File>

</VersionBlock>

<VersionBlock lastVersion="1.11">

### Measures

[Measures](/docs/build/semantic-models#measures) are aggregations performed on columns in your model. Often, you’ll find yourself using them as final metrics themselves. Measures can also serve as building blocks for more complicated metrics.

Add measures to your `fct_orders.yml` semantic model file:

<File name='models/metrics/fct_orders.yml'>

```yaml
semantic_models:
  - name: orders
    defaults:
      agg_time_dimension: order_date
    description: |
      Order fact table. This table’s grain is one row per order.
    model: ref('fct_orders')
    entities:
      - name: order_id
        type: primary
      - name: customer
        expr: customer_id
        type: foreign
    dimensions:
      - name: order_date
        type: time
        type_params:
          time_granularity: day
    # Newly added      
    measures:   
      - name: order_total
        description: The total amount for each order including taxes.
        agg: sum
        expr: amount
      - name: order_count
        expr: 1
        agg: sum
      - name: customers_with_orders
        description: Distinct count of customers placing orders
        agg: count_distinct
        expr: customer_id
      - name: order_value_p99 ## The 99th percentile order value
        expr: amount
        agg: percentile
        agg_params:
          percentile: 0.99
          use_discrete_percentile: True
          use_approximate_percentile: False
```

</File>

</VersionBlock>

<VersionBlock firstVersion="1.12">

### Simple metrics

[Simple metrics](/docs/build/simple) perform an aggregation (like `sum`, `count`, or `average`, and so on) on a single field in your model. They replace the concept of "measures" in previous versions. To define more advanced metrics, refer to [Define metrics and add a second semantic model](/guides/sl-snowflake-qs?step=10).

Add simple metrics to your `fct_orders.yml` model file:

<File name='models/fct_orders.yml'>

```yaml
models:
  - name: fct_orders
    semantic_model:
      enabled: true
      name: orders
      
    agg_time_dimension: order_date
    
    columns:
      # Entities
      - name: order_id
        entity:
          type: primary
      - name: customer_id
        entity:
          name: customer
          type: foreign
      # Dimensions      
      - name: order_date
        granularity: day
        dimension:
          type: time
      - name: amount
        dimension:
          type: categorical
          
    # Newly added - Simple metrics
    metrics:
      - name: order_total
        description: The total amount for each order including taxes
        type: simple
        label: Order total
        agg: sum
        expr: amount
      - name: order_count
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
      - name: order_value_p99
        type: simple
        label: Order value P99
        agg: percentile
        expr: amount
        percentile: 99.0
        percentile_type: discrete
```

</File>

</VersionBlock>

### Configure a time spine

To ensure accurate time-based aggregations, you must configure a [time spine](/docs/build/metricflow-time-spine). The time spine allows you to have accurate metric calculations over different time granularities.

Follow the [MetricFlow time spine guide](/guides/mf-time-spine?step=1) for complete step-by-step instructions on creating and configuring your time spine model. This guide provides the current best practices and avoids deprecated configurations.