## June 2024
- **New:** Introduced new granularity support for cumulative metrics in MetricFlow. Granularity options for cumulative metrics are slightly different than granularity for other metric types. For other metrics, we use the `date_trunc` function to implement granularity. However, because cumulative metrics are non-additive (values can't be added up), we can't use the `date_trunc` function to change their time grain granularity. 
  
  Instead, we use the `first()`, `last()`, and `avg()` aggregation functions to aggregate cumulative metrics over the requested period. By default, we take the first value of the period. You can change this behavior by using the `period_agg` parameter. For more information, refer to [Granularity options for cumulative metrics](/docs/build/cumulative#granularity-options).

#### dbt Semantic Layer
- **New:** Added support for <Term id="predicate-pushdown"/> SQL optimization in MetricFlow. We will now push down categorical dimension filters to the metric source table. Previously filters were applied after we selected from the metric source table. This change helps reduce full table scans on certain query engines.
- **New:** Enabled `where` filters  on dimensions (included in saved queries) to use the cache during query time. This means you can now dynamically filter your dashboards without losing the performance benefits of caching. Refer to [caching](/docs/use-dbt-semantic-layer/sl-cache#result-caching) for more information.
- **Enhancement:** In [Google Sheets](/docs/cloud-integrations/semantic-layer/gsheets), we added information icons and descriptions to metrics and dimensions options in the Query Builder menu. Click on the **Info** icon button to view a description of the metric or dimension. Available in the following Query Builder menu sections: metric, group by, where, saved selections, and saved queries.
- **Enhancement:** In [Google Sheets](/docs/cloud-integrations/semantic-layer/gsheets), you can now apply granularity to all time dimensions, not just metric time. This update uses our [APIs](/docs/dbt-cloud-apis/sl-api-overview) to support granularity selection on any chosen time dimension.
- **Enhancement**: MetricFlow time spine warnings now prompt users to configure missing or small-grain-time spines. An error message is displayed for multiple time spines per granularity.
- **Enhancement**: Errors now display if no time spine is configured at the requested or smaller granularity. 
- **Enhancement:** Improved querying error message when no semantic layer credentials were set.
- **Enhancement:** Querying grains for cumulative metrics now returns multiple granularity options (day, week, month, quarter, year) like all other metric types. Previously, you could only query one grain option for cumulative metrics.
- **Fix:** Removed errors that prevented querying cumulative metrics with other granularities.
- **Fix:** Fixed various Tableau errors when querying certain metrics or when using calculated fields.
- **Fix:** In Tableau, we relaxed naming field expectations to better identify calculated fields.
- **Fix:** Fixed an error when refreshing database metadata for columns that we can't convert to Arrow. These columns will now be skipped. This mainly affected Redshift users with custom types.
- **Fix:** Fixed Private Link connections for Databricks.

#### Also available this month:

- **Enhancement:** Updates to the UI when [creating merge jobs](/docs/deploy/merge-jobs) are now available. The updates include improvements to helper text, new deferral settings, and performance improvements.
- **New**: The <Constant name="semantic_layer" /> now offers a seamless integration with Microsoft Excel, available in [preview](/docs/dbt-versions/product-lifecycles#dbt-cloud). Build semantic layer queries and return data on metrics directly within Excel, through a custom menu. To learn more and install the add-on, check out [Microsoft Excel](/docs/cloud-integrations/semantic-layer/excel).
- **New:** [Job warnings](/docs/deploy/job-notifications) are now GA. Previously, you could receive email or Slack alerts about your jobs when they succeeded, failed, or were canceled. Now with the new **Warns** option, you can also receive alerts when jobs have encountered warnings from tests or source freshness checks during their run. This gives you more flexibility on _when_ to be notified. 
- **New:** A [preview](/docs/dbt-versions/product-lifecycles#dbt-cloud) of the dbt Snowflake Native App is now available. With this app, you can access dbt Explorer, the **Ask dbt** chatbot, and orchestration observability features, extending your <Constant name="dbt" /> experience into the Snowflake UI. To learn more, check out [About the dbt Snowflake Native App](/docs/cloud-integrations/snowflake-native-app) and [Set up the dbt Snowflake Native App](/docs/cloud-integrations/set-up-snowflake-native-app).