## April 2023

- <Expandable alt_header='dbt Cloud IDE'>

    ## New features 

    * New warning message suggests you invoke `dbt deps` when it's needed (as informed by `dbt-score`).
    * New warning message appears when you select models but don't save them before clicking **Build** or invoking dbt (like, dbt build/run/test). 
    * Previews of Markdown and CSV files are now available in the IDE console.
    * The file tree menu now includes a Duplicate File option.
    * Display loading time when previewing a model

    ## Product refinements 

    * Enhance autocomplete experience which has performed slowly for people with large projects and who implement a limit to max `manifest.json` for this feature
    * Introduce pagination for invocation node summary view (displaying 100 nodes at a time)
    * Improve rendering for the Changes / Version Control section of the IDE
    * Update icons to be consistent in dbt Cloud
    * Add table support to the Markdown preview
    * Add the lineage tab back to seed resources in the IDE
    * Implement modal priority when there are multiple warning modals
    * Improve a complex command's description in the command palette

    ## Bug fixes

    * File tree no longer collapses on first click when there's a project subdirectory defined
    * **Revert all** button now works as expected
    * CSV preview no longer fails with only one column
    * Cursor and scroll bar location are now persistent with their positions
    * `git diff` view now shows just change diffs and no longer shows full diff (as if file is new) until page refreshes
    * ToggleMinimap Command no longer runs another Command at the same time
    * `git diff` view no longer shows infinite spins in specific scenarios (new file, etc.)
    * File contents no longer get mixed up when using diff view and one file has unsaved changes
    * YML lineage now renders model without tests (in dbt Core v1.5 and above)
    * Radio buttons for **Summary** and **Details** in the logs section now consistently update to show the accurate tab selection
    * IDE no longer throws the console error `Error: Illegal argument` and redirects to the `Something went wrong` page

  </Expandable>

- <Expandable alt_header='API updates'>

    Starting May 15, 2023, we will support only the following `order_by` functionality for the List Runs endpoint:

    - `id` and `-id`
    - `created_at` and `-created_at`
    - `finished_at` and `-finished_at`

    We recommend that you change your API requests to https://&lt;YOUR_ACCESS_URL&gt;/api/v2/accounts/\{accountId\}/runs/ to use a supported `order_by` before this date. 

    :::info Access URLs
 
    dbt Cloud is hosted in multiple regions around the world, and each region has a different access URL. Users on Enterprise plans can choose to have their account hosted in any one of these regions. For a complete list of available dbt Cloud access URLs, refer to [Regions & IP addresses](/docs/cloud/about-cloud/access-regions-ip-addresses).  

    :::

    For more info, refer to our [documentation](/dbt-cloud/api-v2#/operations/List%20Runs).

  </Expandable>

- <Expandable alt_header='Scheduler optimization'>

    The dbt Cloud Scheduler now prevents queue clog by canceling unnecessary runs of over-scheduled jobs. 

    The duration of a job run tends to grow over time, usually caused by growing amounts of data in the warehouse. If the run duration becomes longer than the frequency of the job’s schedule, the queue will grow faster than the scheduler can process the job’s runs, leading to a runaway queue with runs that don’t need to be processed.

    Previously, when a job was in this over-scheduled state, the scheduler would stop queuing runs after 50 were already in the queue. This led to a poor user experience where the scheduler canceled runs indiscriminately. You’d have to log into dbt Cloud to manually cancel all the queued runs and change the job schedule to "unclog" the scheduler queue.

    Now, the dbt Cloud scheduler detects when a scheduled job is set to run too frequently and appropriately cancels runs that don’t need to be processed. Specifically, scheduled jobs can only ever have one run of the job in the queue, and if a more recent run gets queued, the early queued run will get canceled with a helpful error message. Users will still need to either refactor the job so it runs faster or change the job schedule to run less often if the job often gets into an over-scheduled state.

  </Expandable>

- <Expandable alt_header='Starburst adapter GA'>

    The Starburst (Trino compatible) connection is now generally available in dbt Cloud. This means you can now use dbt Cloud to connect with Starburst Galaxy, Starburst Enterprise, and self-hosted Trino. This feature is powered by the [`dbt-trino`](https://github.com/starburstdata/dbt-trino) adapter. To learn more, check out our Quickstart guide for [dbt Cloud and Starburst Galaxy](/guides/starburst-galaxy).

  </Expandable>

- <Expandable alt_header='Product docs updates'>

    Hello from the dbt Docs team: @mirnawong1, @matthewshaver, @nghi-ly, and @runleonarun! We want to share some highlights introduced to docs.getdbt.com in the last month:

    ## 🔎 Discoverability

    - [API docs](/docs/dbt-cloud-apis/overview) now live in the left sidebar to improve discoverability.
    - [The deploy dbt jobs sidebar](/docs/deploy/deployments) has had a glow up 💅 that splits the ‘about deployment’ into two paths (deploy w dbt cloud and deploy w other tools), adds more info about the dbt cloud scheduler, its features, and how to create a job, adds ADF deployment guidance. We hope the changes improve the user experience and provide users with guidance when deploying with other tools.

    ## ☁ Cloud projects

    - Added Starburst/Trino adapter docs, including:
  * [dbt Cloud quickstart guide](/guides/starburst-galaxy), 
  * [connection page](/docs/cloud/connect-data-platform/connect-starburst-trino), 
  * [set up page](/docs/local/connect-data-platform/trino-setup), and [config page](/reference/resource-configs/trino-configs). 
    - Enhanced [dbt Cloud jobs page](/docs/deploy/jobs) and section to include conceptual info on the queue time, improvements made around it, and about failed jobs. 
    - Check out the April dbt [Cloud release notes](/docs/dbt-versions/dbt-cloud-release-notes)

    ## 🎯 Core projects 

    - Clearer descriptions in the [Jinja functions page](/reference/dbt-jinja-functions-context-variables), that improve content for each card. 
    - [1.5 Docs](/docs/dbt-versions/core-upgrade/Older%20versions/upgrading-to-v1.5) have been released as a Release Candidate (RC)! 
    - See the beautiful [work captured in Core v 1.5](https://github.com/dbt-labs/docs.getdbt.com/issues?q=is%3Aissue+label%3A%22dbt-core+v1.5%22+is%3Aclosed).

    ## New 📚 Guides and ✏️ blog posts

    - [Use Databricks workflows to run dbt Cloud jobs](/guides/how-to-use-databricks-workflows-to-run-dbt-cloud-jobs)
    - [Refresh Tableau workbook with extracts after a job finishes](/guides/zapier-refresh-tableau-workbook)
    - [dbt Python Snowpark workshop/tutorial](/guides/dbt-python-snowpark)
    - [How to optimize and troubleshoot dbt Models on Databricks](/guides/optimize-dbt-models-on-databricks)
    - [The missing guide to debug() in dbt](/blog/guide-to-jinja-debug)
    - [dbt Squared: Leveraging dbt Core and dbt Cloud together at scale](/blog/dbt-squared)
    - [Audit_helper in dbt: Bringing data auditing to a higher level](/blog/audit-helper-for-migration)

  </Expandable>