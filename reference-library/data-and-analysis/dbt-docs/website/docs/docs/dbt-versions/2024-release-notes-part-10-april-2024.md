## April 2024

- <Expandable alt_header="New: Merge jobs" lifecycle="beta" > 

  You can now set up a continuous deployment (CD) workflow for your projects natively in dbt Cloud. You can now access a beta release of [Merge jobs](/docs/deploy/merge-jobs), which is a new [job type](/docs/deploy/jobs), that enables you to trigger dbt job runs as soon as changes (via Git pull requests) merge into production.

  <Lightbox src="/img/docs/dbt-cloud/using-dbt-cloud/example-create-merge-job.png" width="90%" title="Example of creating a merge job"/>

  </Expandable>
  
- **Behavior change:** Introduced the `require_explicit_package_overrides_for_builtin_materializations` flag, opt-in and disabled by default. If set to `True`, dbt will only use built-in materializations defined in the root project or within dbt, rather than implementations in packages. This will become the default in May 2024 (dbt Core v1.8 and dbt Cloud release tracks). Read [Package override for built-in materialization](/reference/global-configs/behavior-changes#package-override-for-built-in-materialization) for more information.

**<Constant name="semantic_layer" />**
- **New**: Use Saved selections to [save your query selections](/docs/cloud-integrations/semantic-layer/gsheets#using-saved-selections) within the [Google Sheets application](/docs/cloud-integrations/semantic-layer/gsheets). They can be made private or public and refresh upon loading.
- **New**: Metrics are now displayed by their labels as `metric_name`.
- **Enhancement**: [Metrics](/docs/build/metrics-overview) now supports the [`meta` option](/reference/resource-configs/meta) under the [config](/reference/resource-properties/config) property. Previously, we only supported the now deprecated `meta` tag.
- **Enhancement**: In the Google Sheets application, we added [support](/docs/cloud-integrations/semantic-layer/gsheets#using-saved-queries) to allow jumping off from or exploring MetricFlow-defined saved queries directly.
- **Enhancement**: In the Google Sheets application, we added support to query dimensions without metrics. Previously, you needed a dimension.
- **Enhancement**: In the Google Sheets application, we added support for time presets and complex time range filters such as "between", "after", and "before".
- **Enhancement**: In the Google Sheets application, we added supported to automatically populate dimension values when you select a "where" filter, removing the need to manually type them.  Previously, you needed to manually type the dimension values.
- **Enhancement**: In the Google Sheets application, we added support to directly query entities, expanding the flexibility of data requests.
- **Enhancement**: In the Google Sheets application, we added an option to exclude column headers, which is useful for populating templates with only the required data.
- **Deprecation**: For the Tableau integration, the [`METRICS_AND_DIMENSIONS` data source](/docs/cloud-integrations/semantic-layer/tableau#using-the-integration) has been deprecated for all accounts not actively using it. We encourage users to transition to the "ALL" data source for future integrations.