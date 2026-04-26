## Set up dbt project

In this section, you will set up a <Constant name="dbt" /> managed repository and initialize your dbt project to start developing.

### Set up a dbt managed repository 
If you used Partner Connect, you can skip to [initializing your dbt project](#initialize-your-dbt-project-and-start-developing) as Partner Connect provides you with a [managed repository](/docs/cloud/git/managed-repository). Otherwise, you will need to create your repository connection. 

<Snippet path="tutorial-managed-repo" />

### Initialize your dbt project
This guide assumes you use the [<Constant name="studio_ide" />](/docs/cloud/studio-ide/develop-in-studio) to develop your dbt project, define metrics, and query and preview metrics using [MetricFlow commands](/docs/build/metricflow-commands).

Now that you have a repository configured, you can initialize your project and start development in <Constant name="dbt" /> using the <Constant name="studio_ide" />:

1. Click **Start developing in the <Constant name="studio_ide" />**. It might take a few minutes for your project to spin up for the first time as it establishes your git connection, clones your repo, and tests the connection to the warehouse.
2. Above the file tree to the left, click **Initialize your project**. This builds out your folder structure with example models.
3. Make your initial commit by clicking **Commit and sync**. Use the commit message `initial commit`. This creates the first commit to your managed repo and allows you to open a branch where you can add a new dbt code.
4. You can now directly query data from your warehouse and execute `dbt run`. You can try this out now:
    - Delete the models/examples folder in the **File <Constant name="catalog" />**.
    - Click **+ Create new file**, add this query to the new file, and click **Save as** to save the new file:
      ```sql
      select * from raw.jaffle_shop.customers
      ```
    - In the command line bar at the bottom, enter dbt run and click Enter. You should see a dbt run succeeded message.