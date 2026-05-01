## December 2024

- **New**: Saved queries now support [tags](/reference/resource-configs/tags), which allow you to categorize your resources and filter them. Add tags to your [saved queries](/docs/build/saved-queries) in the `semantic_model.yml` file or `dbt_project.yml` file. For example:
  <File name='dbt_project.yml'>

  ```yml
  [saved-queries](/docs/build/saved-queries):
    jaffle_shop:
      customer_order_metrics:
        +tags: order_metrics
  ```
  </File>
- **New**: [Dimensions](/reference/resource-configs/meta) now support the `meta` config property in [dbt Cloud **Latest** release track](/docs/dbt-versions/cloud-release-tracks) and from dbt Core 1.9. You can add metadata to your dimensions to provide additional context and information about the dimension. Refer to [meta](/reference/resource-configs/meta) for more information.
- **New**: [Downstream exposures](/docs/cloud-integrations/downstream-exposures-tableau) are now generally available to <Constant name="dbt" /> Enterprise plans. Downstream exposures integrate natively with Tableau (Power BI coming soon) and auto-generate downstream lineage in dbt Explorer for a richer experience.
- **New**: The <Constant name="semantic_layer" /> supports Sigma as a [partner integration](/docs/cloud-integrations/avail-sl-integrations), available in Preview. Refer to [Sigma](https://help.sigmacomputing.com/docs/configure-a-dbt-semantic-layer-integration) for more information.
- **New**: The <Constant name="semantic_layer" /> now supports Azure Single-tenant deployments. Refer to [Set up the <Constant name="semantic_layer" />](/docs/use-dbt-semantic-layer/setup-sl) for more information on how to get started.
- **Fix**: Resolved intermittent issues in Single-tenant environments affecting <Constant name="semantic_layer" /> and query history.
- **Fix**: [The dbt Semantic Layer](/docs/use-dbt-semantic-layer/dbt-sl) now respects the BigQuery [`execution_project` attribute](/docs/local/connect-data-platform/bigquery-setup#execution-project), including for exports.
- **New**: [Model notifications](/docs/deploy/model-notifications) are now generally available in <Constant name="dbt" />. These notifications alert model owners through email about any issues encountered by models and tests as soon as they occur while running a job.
- **New**: You can now use your [Azure OpenAI key](/docs/cloud/account-integrations?ai-integration=azure#ai-integrations) (available in beta) to use <Constant name="dbt" /> features like [<Constant name="copilot" />](/docs/cloud/dbt-copilot) and [Ask dbt](/docs/cloud-integrations/snowflake-native-app) . Additionally, you can use your own [OpenAI API key](/docs/cloud/account-integrations?ai-integration=openai#ai-integrations) or use [dbt Labs-managed OpenAI](/docs/cloud/account-integrations?ai-integration=dbtlabs#ai-integrations) key. Refer to [AI integrations](/docs/cloud/account-integrations#ai-integrations) for more information.
- **New**: The [`hard_deletes`](/reference/resource-configs/hard-deletes) config gives you more control on how to handle deleted rows from the source. Supported options are `ignore` (default), `invalidate` (replaces the legacy `invalidate_hard_deletes=true`), and `new_record`. Note that `new_record` will create a new metadata column in the snapshot table.