## February 2024

- **New:** [Exports](/docs/use-dbt-semantic-layer/exports#define-exports) allow you to materialize a saved query as a table or view in your data platform. By using exports, you can unify metric definitions in your data platform and query them as you would any other table or view.
- **New:** You can access a list of your [exports](/docs/use-dbt-semantic-layer/exports) with the new list saved-queries command by adding `--show-exports`
- **New:** The <Constant name="semantic_layer" /> and [Tableau Connector](/docs/cloud-integrations/semantic-layer/tableau) now supports relative date filters in Tableau.

- <Expandable alt_header="New: Use exports to write saved queries">

  You can now use the [exports](/docs/use-dbt-semantic-layer/exports) feature with [dbt Semantic Layer](/docs/use-dbt-semantic-layer/dbt-sl), allowing you to query reliable metrics and fast data reporting. Exports enhance the saved queries feature, allowing you to write commonly used queries directly within your data platform using dbt Cloud's job scheduler.

  By exposing tables of metrics and dimensions, exports enable you to integrate with additional tools that don't natively connect with the dbt Semantic Layer, such as PowerBI.

  Exports are available for dbt Cloud multi-tenant [Team or Enterprise](https://www.getdbt.com/pricing/) plans on dbt versions 1.7 or newer. Refer to the [exports blog](https://www.getdbt.com/blog/announcing-exports-for-the-dbt-semantic-layer) for more details.

  <Lightbox src="/img/docs/dbt-cloud/semantic-layer/deploy_exports.png" width="90%" title="Add an environment variable to run exports in your production run." />

  </Expandable>

- <Expandable alt_header="New: Trigger on job completion " lifecycle="team,enterprise" >

  Now available for dbt Cloud Team and Enterprise plans is the ability to trigger deploy jobs when other deploy jobs are complete. You can enable this feature [in the UI](/docs/deploy/deploy-jobs) with the  **Run when another job finishes** option in the **Triggers** section of your job or with the [Create Job API endpoint](/dbt-cloud/api-v2#/operations/Create%20Job). 

  When enabled, your job will run after the specified upstream job completes. You can configure which run status(es) will trigger your job. It can be just on `Success` or on all statuses. If you have dependencies between your dbt projects, this allows you to _natively_ orchestrate your jobs within dbt Cloud &mdash; no need to set up a third-party tool.

  An example of the **Triggers** section when creating the job:  

  <Lightbox src="/img/docs/dbt-cloud/using-dbt-cloud/example-triggers-section.png" width="90%" title="Example of Triggers on the Deploy Job page"/>

  </Expandable>

- <Expandable alt_header="New: Latest Release Track" lifecycle="beta">

  _Now available in the dbt version dropdown in dbt Cloud &mdash; starting with select customers, rolling out to wider availability through February and March._

  On this release track, you get automatic upgrades of dbt, including early access to the latest features, fixes, and performance improvements for your dbt project. dbt Labs will handle upgrades behind-the-scenes, as part of testing and redeploying the dbt Cloud application &mdash; just like other dbt Cloud capabilities and other SaaS tools that you're using. No more manual upgrades and no more need for _a second sandbox project_ just to try out new features in development.

  To learn more about the new setting, refer to [Release Tracks](/docs/dbt-versions/cloud-release-tracks) for details. 

  <Lightbox src="/img/docs/dbt-cloud/cloud-configuring-dbt-cloud/choosing-dbt-version/example-environment-settings.png" width="90%" title="Example of the Latest setting"/>

  </Expandable>


- <Expandable alt_header="New: Override dbt version with new User development settings" >

  You can now [override the dbt version](/docs/dbt-versions/upgrade-dbt-version-in-cloud#override-dbt-version) that's configured for the development environment within your project and use a different version &mdash; affecting only your user account. This lets you test new dbt features without impacting other people working on the same project. And when you're satisfied with the test results, you can safely upgrade the dbt version for your project(s).  

  Use the **dbt version** dropdown to specify the version to override with. It's available on your project's credentials page in the **User development settings** section. For example:

  <Lightbox src="/img/docs/dbt-cloud/cloud-configuring-dbt-cloud/choosing-dbt-version/example-override-version.png" width="60%" title="Example of overriding the dbt version on your user account"/>

  </Expandable>

- <Expandable alt_header="Enhancement: Edit in primary git branch in IDE">

  You can now edit, format, or lint files and execute dbt commands directly in your primary git branch in the [dbt Cloud IDE](/docs/cloud/studio-ide/develop-in-studio).  This enhancement is available across various repositories, including native integrations, imported git URLs, and managed repos.

  This enhancement is currently available to all dbt Cloud multi-tenant regions and will soon be available to single-tenant accounts.

  The primary branch of the connected git repo has traditionally been _read-only_ in the IDE. This update changes the branch to _protected_ and allows direct edits. When a commit is made, dbt Cloud will prompt you to create a new branch. dbt Cloud will pre-populate the new branch name with the GIT_USERNAME-patch-#; however, you can edit the field with a custom branch name.

  Previously, the primary branch was displayed as read-only, but now the branch is displayed with a lock icon to identify it as protected:

  <DocCarousel slidesPerView={1}>

  <Lightbox src="/img/docs/dbt-cloud/using-dbt-cloud/read-only.png" width="75%" title="Previous read-only experience"/>

  <Lightbox src="/img/docs/dbt-cloud/using-dbt-cloud/protected.png" width="75%" title="New protected experience"/>

  </DocCarousel>

  When you make a commit while on the primary branch, a modal window will open prompting you to create a new branch and enter a commit message:

  <Lightbox src="/img/docs/dbt-cloud/using-dbt-cloud/create-new-branch.png" width="75%" title="Create new branch window"/>

  </Expandable>

- **Enhancement:**  The <Constant name="semantic_layer" /> [Google Sheets integration](/docs/cloud-integrations/semantic-layer/gsheets) now exposes a note on the cell where the data was requested, indicating clearer data requests. The integration also now exposes a new **Time Range** option, which allows you to quickly select date ranges.
- **Enhancement:** The [GraphQL API](/docs/dbt-cloud-apis/sl-graphql) includes a `requiresMetricTime` parameter to better handle metrics that must be grouped by time. (Certain metrics defined in MetricFlow can't be looked at without a time dimension).
- **Enhancement:** Enable querying metrics with offset and cumulative metrics with the time dimension name, instead of `metric_time`. [Issue #1000](https://github.com/dbt-labs/metricflow/issues/1000)
  - Enable querying `metric_time` without metrics. [Issue #928](https://github.com/dbt-labs/metricflow/issues/928)
- **Enhancement:** Added support for consistent SQL query generation, which enables ID generation consistency between otherwise identical MF queries. Previously, the SQL generated by `MetricFlowEngine` was not completely consistent between identical queries. [Issue 1020](https://github.com/dbt-labs/metricflow/issues/1020)
- **Fix:** The Tableau Connector returns a date filter when filtering by dates. Previously it was erroneously returning a timestamp filter.
-  **Fix:** MetricFlow now validates if there are `metrics`, `group by`, or `saved_query` items in each query. Previously, there was no validation. [Issue 1002](https://github.com/dbt-labs/metricflow/issues/1002)
- **Fix:** Measures using `join_to_timespine` in MetricFlow now have filters applied correctly after time spine join.
- **Fix:** Querying multiple granularities with offset metrics:
  - If you query a time offset metric with multiple instances of `metric_time`/`agg_time_dimension`, only one of the instances will be offset. All of them should be.
  - If you query a time offset metric with one instance of `metric_time`/`agg_time_dimension` but filter by a different one, the query will fail.
- **Fix:** MetricFlow prioritizes a candidate join type over the default type when evaluating nodes to join. For example, the default join type for distinct values queries is `FULL OUTER JOIN`, however, time spine joins require `CROSS JOIN`, which is more appropriate.
- **Fix:** Fixed a bug that previously caused errors when entities were referenced in `where` filters.