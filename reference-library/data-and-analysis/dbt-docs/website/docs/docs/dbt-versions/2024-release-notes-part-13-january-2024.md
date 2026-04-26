## January 2024

- <Expandable alt_header="January docs updates">

  Hello from the dbt Docs team: @mirnawong1, @matthewshaver, @nghi-ly, and @runleonarun! First, we’d like to thank the 10 new community contributors to docs.getdbt.com :pray: What a busy start to the year! We merged 110 PRs in January.

  Here's how we improved the [docs.getdbt.com](http://docs.getdbt.com/) experience:

  - Added new hover behavior for images
  - Added new expandables for FAQs
  - Pruned outdated notices and snippets as part of the docs site maintenance

  January saw some great new content:

  - New [dbt Mesh FAQs](/best-practices/how-we-mesh/mesh-5-faqs) page
  - Beta launch of [Explorer’s column-level lineage](/docs/explore/column-level-lineage) feature
  - Developer blog posts:
    - [More time coding, less time waiting: Mastering defer in dbt](/blog/defer-to-prod)
    - [Deprecation of dbt Server](/blog/deprecation-of-dbt-server)
    - From the community: [Serverless, free-tier data stack with dlt + dbt core](/blog/serverless-dlt-dbt-stack)
  - The Extrica team added docs for the [dbt-extrica community adapter](/docs/local/connect-data-platform/extrica-setup)
  - Semantic Layer: New [conversion metrics docs](/docs/build/conversion) and added the parameter `fill_nulls_with` to all metric types (launched the week of January 12, 2024)
  - New [dbt environment command](/reference/commands/dbt-environment) and its flags for the dbt CLI

  January also saw some refreshed content, either aligning with new product features or requests from the community:

  - Native support for [partial parsing in dbt Cloud](/docs/cloud/account-settings#partial-parsing)
  - Updated guidance on using dots or underscores in the [Best practice guide for models](/best-practices/how-we-style/1-how-we-style-our-dbt-models)
  - Updated [PrivateLink for VCS docs](/docs/cloud/secure/private-connectivity/aws/aws-self-hosted)
  - Added a new `job_runner` role in our [Enterprise project role permissions docs](/docs/cloud/manage-access/enterprise-permissions#project-role-permissions)
  - Added saved queries to [Metricflow commands](/docs/build/metricflow-commands#list-saved-queries)
  - Removed [as_text docs](https://github.com/dbt-labs/docs.getdbt.com/pull/4726) that were wildly outdated

  </Expandable>

- **New:** New metric type that allows you to measure conversion events. For example, users who viewed a web page and then filled out a form. For more details, refer to [Conversion metrics](/docs/build/conversion). 
- **New:** Instead of specifying the fully qualified dimension name (for example, `order__user__country`) in the group by or filter expression, you now only need to provide the primary entity and dimensions name, like `user__county`. 
- **New:** You can now query the [saved queries](/docs/build/saved-queries) you've defined in the <Constant name="semantic_layer" /> using [Tableau](/docs/cloud-integrations/semantic-layer/tableau), [GraphQL API](/docs/dbt-cloud-apis/sl-graphql), [JDBC API](/docs/dbt-cloud-apis/sl-jdbc), and the [<Constant name="dbt" /> CLI](/docs/cloud/cloud-cli-installation). 

- <Expandable alt_header="New: Native support for partial parsing" >

  By default, dbt parses all the files in your project at the beginning of every dbt invocation. Depending on the size of your project, this operation can take a long time to complete. With the new partial parsing feature in dbt Cloud, you can reduce the time it takes for dbt to parse your project. When enabled, dbt Cloud parses only the changed files in your project instead of parsing all the project files. As a result, your dbt invocations will take less time to run.

  To learn more, refer to [Partial parsing](/docs/cloud/account-settings#partial-parsing).

  <Lightbox src="/img/docs/deploy/account-settings-partial-parsing.png" width="85%" title="Example of the Partial parsing option" />

  </Expandable>

- **Enhancement:** The YAML spec parameter `label` is now available for Semantic Layer metrics in [JDBC and GraphQL APIs](/docs/dbt-cloud-apis/sl-api-overview). This means you can conveniently use `label` as a display name for your metrics when exposing them.
- **Enhancement:** Added support for `create_metric: true` for a measure, which is a shorthand to quickly create metrics. This is useful in cases when metrics are only used to build other metrics.
- **Enhancement:** Added support for Tableau parameter filters. You can use the [Tableau connector](/docs/cloud-integrations/semantic-layer/tableau) to create and use parameters with your <Constant name="semantic_layer" /> data.
- **Enhancement:** Added support to expose `expr` and `agg` for [Measures](/docs/build/measures) in the [GraphQL API](/docs/dbt-cloud-apis/sl-graphql).
- **Enhancement:** You have improved error messages in the command line interface when querying a dimension that is not reachable for a given metric.
- **Enhancement:** You can now query entities using our Tableau integration (similar to querying dimensions). 
- **Enhancement:** A new data source is available in our Tableau integration called "ALL", which contains all semantic objects defined. This has the same information as "METRICS_AND_DIMENSIONS". In the future, we will deprecate "METRICS_AND_DIMENSIONS" in favor of "ALL" for clarity. 

- **Fix:** Support for numeric types with precision greater than 38 (like `BIGDECIMAL`) in BigQuery is now available. Previously, it was unsupported so would return an error.
- **Fix:** In some instances, large numeric dimensions were being interpreted by Tableau in scientific notation, making them hard to use. These should now be displayed as numbers as expected.
- **Fix:** We now preserve dimension values accurately instead of being inadvertently converted into strings. 
- **Fix:** Resolved issues with naming collisions in queries involving multiple derived metrics using the same metric input. Previously, this  could cause a naming collision. Input metrics are now deduplicated, ensuring each is referenced only once.
- **Fix:** Resolved warnings related to using two duplicate input measures in a derived metric. Previously, this would trigger a warning. Input measures are now deduplicated, enhancing query processing and clarity.
- **Fix:** Resolved an error where referencing an entity in a filter using the object syntax would fail. For example, `{{Entity('entity_name')}}` would fail to resolve.