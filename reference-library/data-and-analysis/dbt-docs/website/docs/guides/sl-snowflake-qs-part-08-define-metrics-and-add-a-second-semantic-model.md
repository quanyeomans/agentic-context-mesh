## Define metrics and add a second semantic model

In this section, you will [define metrics](#define-metrics) and [add a second semantic model](#add-second-semantic-model-to-your-project) to your project.

### Define metrics

[Metrics](/docs/build/metrics-overview) are the language your business users speak and measure business performance. They are an aggregation over a column in your warehouse that you enrich with dimensional cuts.

There are different types of metrics you can configure:

- [Conversion metrics](/docs/build/conversion) &mdash; Track when a base event and a subsequent conversion event occur for an entity within a set time period.
- [Cumulative metrics](/docs/build/cumulative) &mdash; Aggregate a measure over a given window. If no window is specified, the window will accumulate the measure over all of the recorded time period. Note that you must create the time spine model before you add cumulative metrics.
- [Derived metrics](/docs/build/metrics-overview#derived-metrics) &mdash; Allows you to do calculations on top of metrics.
- [Simple metrics](/docs/build/metrics-overview#simple-metrics) &mdash; Directly reference a single column expression within a semantic model, without any additional columns involved. They are aggregations over a column in your data platform and can be filtered by one or multiple dimensions.
- [Ratio metrics](/docs/build/metrics-overview#ratio-metrics) &mdash; Involve a numerator metric and a denominator metric. A constraint string can be applied to both the numerator and denominator or separately to the numerator or denominator.

Once you've created your semantic models, it's time to start referencing those <VersionBlock lastVersion="1.11">measures</VersionBlock><VersionBlock firstVersion="1.12">simple metrics</VersionBlock> you made to create some metrics:

1. Add metrics to your `fct_orders.yml` file:

:::tip 
Make sure to save all semantic models and metrics under the directory defined in the [`model-paths`](/reference/project-configs/model-paths) (or a subdirectory of it, like `models/semantic_models/`). If you save them outside of this path, it will result in an empty `semantic_manifest.json` file, and your semantic models or metrics won't be recognized.
:::

<VersionBlock lastVersion="1.11">

<File name='models/metrics/fct_orders.yml'>

```yaml
semantic_models:
  - name: orders
    defaults:
      agg_time_dimension: order_date
    description: |
      Order fact table. This table’s grain is one row per order
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
      - name: order_value_p99
        expr: amount
        agg: percentile
        agg_params:
          percentile: 0.99
          use_discrete_percentile: True
          use_approximate_percentile: False