## May 2024

- **Enhancement:** We've now introduced a new **Prune branches** [<Constant name="git" /> button](/docs/cloud/studio-ide/ide-user-interface#prune-branches-modal) in the IDE. This button allows you to delete local branches that have been deleted from the remote repository, keeping your branch management tidy. Available in all regions now and will be released to single tenant accounts during the next release cycle.

#### dbt Cloud Launch Showcase event

The following features are new or enhanced as part of our [<Constant name="dbt" /> Launch Showcase](https://www.getdbt.com/resources/webinars/dbt-cloud-launch-showcase) event on May 14th, 2024:

- **New:** [<Constant name="copilot" />](/docs/cloud/dbt-copilot) is a powerful AI engine helping you generate documentation, tests, and semantic models, saving you time as you deliver high-quality data. Available in private beta for a subset of <Constant name="dbt" /> Enterprise users and in the IDE. [Register your interest](https://docs.google.com/forms/d/e/1FAIpQLScPjRGyrtgfmdY919Pf3kgqI5E95xxPXz-8JoVruw-L9jVtxg/viewform) to join the private beta.

- **New:** The new low-code editor, now in private beta, enables less SQL-savvy analysts to create or edit dbt models through a visual, drag-and-drop experience inside of <Constant name="dbt" />. These models compile directly to SQL and are indistinguishable from other dbt models in your projects: they are version-controlled, can be accessed across projects in <Constant name="mesh" />, and integrate with dbt Explorer and the Cloud IDE. [Register your interest](https://docs.google.com/forms/d/e/1FAIpQLScPjRGyrtgfmdY919Pf3kgqI5E95xxPXz-8JoVruw-L9jVtxg/viewform) to join the private beta.

- **New:** [<Constant name="dbt" /> CLI](/docs/cloud/cloud-cli-installation) is now Generally Available (GA) to all users. The <Constant name="dbt" /> CLI is a command-line interface that allows you to interact with <Constant name="dbt" />, use automatic deferral, leverage <Constant name="mesh" />, and more!

- **New:** [Unit tests](/docs/build/unit-tests) are now GA in <Constant name="dbt" />. Unit tests enable you to test your SQL model logic against a set of static inputs.

- <Expandable alt_header="New: Native support for Azure Synapse Analytics" lifecycle="preview"> 

  Native support in dbt Cloud for Azure Synapse Analytics is now available as a [preview](/docs/dbt-versions/product-lifecycles#dbt-cloud)!

  To learn more, refer to [Connect Azure Synapse Analytics](/docs/cloud/connect-data-platform/connect-azure-synapse-analytics) and [Microsoft Azure Synapse DWH configurations](/reference/resource-configs/azuresynapse-configs).

  Also, check out the [Quickstart for dbt Cloud and Azure Synapse Analytics](/guides/azure-synapse-analytics?step=1). The guide walks you through:

  - Loading the Jaffle Shop sample data (provided by dbt Labs) into Azure Synapse Analytics. 
  - Connecting dbt Cloud to Azure Synapse Analytics.
  - Turning a sample query into a model in your dbt project. A model in dbt is a SELECT statement.
  - Adding tests to your models.
  - Documenting your models.
  - Scheduling a job to run.

  </Expandable>

- **New:** MetricFlow enables you to now add metrics as dimensions to your metric filters to create more complex metrics and gain more insights. Available for all <Constant name="semantic_layer" /> users.

- **New:** [Staging environment](/docs/deploy/deploy-environments#staging-environment) is now GA. Use staging environments to grant developers access to deployment workflows and tools while controlling access to production data. Available to all <Constant name="dbt" /> users.

- **New:** Oauth login support via [Databricks](/docs/cloud/manage-access/set-up-databricks-oauth) is now GA to Enterprise customers.

- <Expandable alt_header="New: GA of dbt Explorer's features" > 

  dbt Explorer's current capabilities &mdash; including column-level lineage, model performance analysis, and project recommendations &mdash; are now Generally Available for dbt Cloud Enterprise and Teams plans. With Explorer, you can more easily navigate your dbt Cloud project – including models, sources, and their columns – to gain a better understanding of its latest production or staging state.

  To learn more about its features, check out:
  
  - [Explore projects](/docs/explore/explore-projects)
  - [Explore multiple projects](/docs/explore/explore-multiple-projects) 
  - [Column-level lineage](/docs/explore/column-level-lineage) 
  - [Model performance](/docs/explore/model-performance) 
  - [Project recommendations](/docs/explore/project-recommendations) 

  </Expandable>

- **New:** Native support for Microsoft Fabric in <Constant name="dbt" /> is now GA. This feature is powered by the [dbt-fabric](https://github.com/Microsoft/dbt-fabric) adapter. To learn more, refer to [Connect Microsoft Fabric](/docs/cloud/connect-data-platform/connect-microsoft-fabric) and [Microsoft Fabric DWH configurations](/reference/resource-configs/fabric-configs). There's also a [quickstart guide](/guides/microsoft-fabric?step=1) to help you get started. 

- **New:** <Constant name="mesh" /> is now GA to <Constant name="dbt" /> Enterprise users. <Constant name="mesh" /> is a framework that helps organizations scale their teams and data assets effectively. It promotes governance best practices and breaks large projects into manageable sections. Get started with <Constant name="mesh" /> by reading the [<Constant name="mesh" /> quickstart guide](/guides/mesh-qs?step=1).

- **New:** The <Constant name="semantic_layer" /> [Tableau Desktop, Tableau Server](/docs/cloud-integrations/semantic-layer/tableau), and [Google Sheets integration](/docs/cloud-integrations/semantic-layer/gsheets) is now GA to <Constant name="dbt" /> Team or Enterprise accounts. These first-class integrations allow you to query and unlock valuable insights from your data ecosystem.

- **Enhancement:** As part of our ongoing commitment to improving the [IDE](/docs/cloud/studio-ide/develop-in-studio#considerations), the filesystem now comes with improvements to speed up dbt development, such as introducing a <Constant name="git" /> repository limit of 10GB.

#### Also available this month:

- **Update**: The [<Constant name="dbt" /> CLI](/docs/cloud/cloud-cli-installation) is now available for Azure single tenant and is accessible in all [deployment regions](/docs/cloud/about-cloud/access-regions-ip-addresses) for both multi-tenant and single-tenant accounts.

- **New**: The [<Constant name="semantic_layer" />](/docs/use-dbt-semantic-layer/dbt-sl) introduces [declarative caching](/docs/use-dbt-semantic-layer/sl-cache), allowing you to cache common queries to speed up performance and reduce query compute costs. Available for <Constant name="dbt" /> Team or Enterprise accounts.

- <Expandable alt_header="New: Latest Release Track" > 

  The **Latest** Release Track is now Generally Available (previously Public Preview).

  On this release track, you get automatic upgrades of dbt, including early access to the latest features, fixes, and performance improvements for your dbt project. dbt Labs will handle upgrades behind-the-scenes, as part of testing and redeploying the dbt Cloud application &mdash; just like other dbt Cloud capabilities and other SaaS tools that you're using. No more manual upgrades and no more need for _a second sandbox project_ just to try out new features in development.

  To learn more about the new setting, refer to [Release Tracks](/docs/dbt-versions/cloud-release-tracks) for details. 

  <Lightbox src="/img/docs/dbt-cloud/cloud-configuring-dbt-cloud/choosing-dbt-version/example-environment-settings.png" width="90%" title="Example of the Latest setting"/>

  </Expandable>

- **Behavior change:** Introduced the `require_resource_names_without_spaces` flag, opt-in and disabled by default. If set to `True`, dbt will raise an exception if it finds a resource name containing a space in your project or an installed package. This will become the default in a future version of dbt. Read [No spaces in resource names](/reference/global-configs/behavior-changes#no-spaces-in-resource-names) for more information.