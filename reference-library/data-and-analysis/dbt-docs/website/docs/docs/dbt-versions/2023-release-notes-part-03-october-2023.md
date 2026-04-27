## October 2023

- <Expandable alt_header='dbt Cloud APIs'>

    Beginning December 1, 2023, the [Administrative API](/docs/dbt-cloud-apis/admin-cloud-api) v2 and v3 will expect you to limit all "list" or `GET` API methods to 100 results per API request. This limit enhances the efficiency and stability of our services. If you need to handle more than 100 results, then use the `limit` and `offset` query parameters to paginate those results; otherwise, you will receive an error. 

    This maximum limit applies to [multi-tenant instances](/docs/cloud/about-cloud/access-regions-ip-addresses) only, and _does not_ apply to single tenant instances.

    Refer to the [API v3 Pagination](/dbt-cloud/api-v3#/) or [API v2 Pagination](/dbt-cloud/api-v2#/) sections for more information on how to paginate your API responses. 

  </Expandable>

- <Expandable alt_header='dbt CLI'>

    We are excited to announce the dbt CLI, **unified command line for dbt**, is available in public preview. It’s a local development experience, powered by dbt Cloud.  It’s easy to get started:  `pip3 install dbt` or `brew install dbt` and you’re ready to go.

    We will continue to invest in the dbt Cloud IDE as the easiest and most accessible way to get started using dbt, especially for data analysts who have never developed software using the command line before. We will keep improving the speed, stability, and feature richness of the IDE, as we have been [all year long](https://www.getdbt.com/blog/improvements-to-the-dbt-cloud-ide/).

    We also know that many people developing in dbt have a preference for local development, where they can use their favorite terminal, text editor, keybindings, color scheme, and so on. This includes people with data engineering backgrounds, as well as those analytics engineers who started writing code in the dbt Cloud IDE and have expanded their skills. 

    The new dbt CLI offers the best of both worlds, including: 

    - The power of developing against the dbt Cloud platform 
    - The flexibility of your own local setup

    Run whichever community-developed plugins, pre-commit hooks, or other arbitrary scripts you like.

    Some of the unique capabilities of this dbt CLI include:

    - Automatic deferral of build artifacts to your Cloud project's production environment
    - Secure credential storage in the dbt Cloud platform
    - Support for dbt Mesh ([cross-project `ref`](/docs/mesh/govern/project-dependencies))
    - Development workflow for dbt Semantic Layer
    - Speedier, lower cost builds

    Refer to [dbt CLI](/docs/cloud/cloud-cli-installation) to learn more.

  </Expandable>

- <Expandable alt_header='Custom branch fix'>

    If you don't set a [custom branch](/docs/dbt-cloud-environments#custom-branch-behavior) for your dbt Cloud environment, it now defaults to the default branch of your Git repository (for example, `main`). Previously, [CI jobs](/docs/deploy/ci-jobs) would run for pull requests (PRs) that were opened against _any branch_ or updated with new commits if the **Custom Branch** option wasn't set. 

    ## Azure DevOps 

    Your Git pull requests (PRs) might not trigger against your default branch if you're using Azure DevOps and the default branch isn't `main` or `master`. To resolve this, [set up a custom branch](/faqs/Environments/custom-branch-settings) with the branch you want to target.  

  </Expandable>

- <Expandable alt_header='dbt deps auto install'>

    The dbt Cloud IDE and dbt CLI now automatically installs `dbt deps` when your environment starts or when necessary. Previously, it would prompt you to run `dbt deps` during initialization. 

    This improved workflow is available to all multi-tenant dbt Cloud users (Single-tenant support coming next week) and applies to dbt versions.

    However, you should still run the `dbt deps` command in these situations:

    - When you make changes to the `packages.yml` or `dependencies.yml` file during a session
    - When you update the package version in the `packages.yml` or `dependencies.yml` file. 
    - If you edit the `dependencies.yml` file and the number of packages remains the same, run `dbt deps`. (Note that this is a known bug dbt Labs will fix in the future.)

  </Expandable>

- <Expandable alt_header='Native retry support'>

    Previously in dbt Cloud, you could only rerun an errored job from start but now you can also rerun it from its point of failure. 

    You can view which job failed to complete successfully, which command failed in the run step, and choose how to rerun it. To learn more, refer to [Retry jobs](/docs/deploy/retry-jobs).

    <Lightbox src="/img/docs/deploy/native-retry.gif" width="70%" title="Example of the Rerun options in dbt Cloud"/>

  </Expandable>

- <Expandable alt_header='Product docs updates'>

    Hello from the dbt Docs team: @mirnawong1, @matthewshaver, @nghi-ly, and @runleonarun! First, we’d like to thank the 15 new community contributors to docs.getdbt.com. We merged [107 PRs](https://github.com/dbt-labs/docs.getdbt.com/pulls?q=is%3Apr+merged%3A2023-09-01..2023-09-31) in September.

    Here's what's new to [docs.getdbt.com](http://docs.getdbt.com/):

    * Migrated docs.getdbt.com from Netlify to Vercel.

    ## ☁ Cloud projects
    - Continuous integration jobs are now generally available and no longer in beta!
    - Added [Postgres PrivateLink set up page](/docs/cloud/secure/private-connectivity/aws/aws-postgres)
    - Published beta docs for [dbt Explorer](/docs/explore/explore-projects).
    - Added a new Semantic Layer [GraphQL API doc](/docs/dbt-cloud-apis/sl-graphql) and updated the [integration docs](/docs/cloud-integrations/avail-sl-integrations) to include Hex. Responded to dbt community feedback and clarified Metricflow use cases for dbt Core and dbt Cloud.
    - Added an [FAQ](/faqs/Git/git-migration) describing how to migrate from one git provider to another in dbt Cloud.
    - Clarified an example and added a [troubleshooting section](/docs/cloud/connect-data-platform/connect-snowflake#troubleshooting) to Snowflake connection docs to address common errors and provide solutions.

    ## 🎯 Core projects

    - Deprecated dbt Core v1.0 and v1.1 from the docs.
    - Added configuration instructions for the [AWS Glue](/docs/local/connect-data-platform/glue-setup) community plugin.
    - Revised the dbt Core quickstart, making it easier to follow. Divided this guide into steps that align with the [other guides](/guides/manual-install?step=1).

    ## New 📚 Guides, ✏️ blog posts, and FAQs

    Added a [style guide template](/best-practices/how-we-style/6-how-we-style-conclusion#style-guide-template) that you can copy & paste to make sure you adhere to best practices when styling dbt projects!

    ## Upcoming changes

    Stay tuned for a flurry of releases in October and a filterable guides section that will make guides easier to find!

  </Expandable>

- <Expandable alt_header='Semantic layer GA'>
  
    If you're using the legacy Semantic Layer, we _highly_ recommend you [upgrade your dbt version](/docs/dbt-versions/upgrade-dbt-version-in-cloud) to dbt v1.6 or higher and migrate to the latest Semantic Layer.

    dbt Labs is thrilled to announce that the [dbt Semantic Layer](/docs/use-dbt-semantic-layer/dbt-sl) is now generally available. It offers consistent data organization, improved governance, reduced costs, enhanced efficiency, and accessible data for better decision-making and collaboration across organizations.

    It aims to bring the best of modeling and semantics to downstream applications by introducing:

    - Brand new [integrations](/docs/cloud-integrations/avail-sl-integrations) such as Tableau, Google Sheets, Hex, Mode, and Lightdash.
    - New [Semantic Layer APIs](/docs/dbt-cloud-apis/sl-api-overview) using GraphQL and JDBC to query metrics and build integrations.
    - dbt Cloud [multi-tenant regional](/docs/cloud/about-cloud/access-regions-ip-addresses) support for North America, EMEA, and APAC. Single-tenant support coming soon.
    - Coming soon &mdash; Schedule exports (a way to build tables in your data platform) as part of your dbt Cloud job. Use the APIs to call an export, then access them in your preferred BI tool.  

    <Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-architecture.jpg" width="80%" title="Use the universal dbt Semantic Layer to define and queried metrics in integration tools."/>

    The dbt Semantic Layer is available to [dbt Cloud Team or Enterprise](https://www.getdbt.com/) multi-tenant plans on dbt v1.6 or higher. 
    - Team and Enterprise customers can use 1,000 Queried Metrics per month for no additional cost on a limited trial basis, subject to reasonable use limitations. Refer to [Billing](/docs/cloud/billing#what-counts-as-a-queried-metric) for more information.
    - <Constant name="dbt" /> Developer plans and <Constant name="core" /> users can define metrics but won't be able to query them with integrated tools.

  </Expandable>