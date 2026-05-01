## Connect dbt to Snowflake

There are two ways to connect <Constant name="dbt" /> to Snowflake. The first option is Partner Connect, which provides a streamlined setup to create your <Constant name="dbt" /> account from within your new Snowflake trial account. The second option is to create your <Constant name="dbt" /> account separately and build the Snowflake connection yourself (connect manually). If you want to get started quickly, dbt Labs recommends using Partner Connect. If you want to customize your setup from the very beginning and gain familiarity with the <Constant name="dbt" /> setup flow, dbt Labs recommends connecting manually.

<Tabs>
<TabItem value="partner-connect" label="Use Partner Connect" default>

Using Partner Connect allows you to create a complete dbt account with your [Snowflake connection](/docs/cloud/connect-data-platform/connect-snowflake), [a managed repository](/docs/cloud/git/managed-repository), [environments](/docs/build/custom-schemas#managing-environments), and credentials.

1. On the left sidebar of the Snowflake UI, go to **Admin > Partner Connect**. Find the dbt tile under the **Data Integration** section or search for dbt in the search bar. Click the tile to connect to dbt.

    <Lightbox src="/img/snowflake_tutorial/snowflake_partner_connect_box.png" width="60%" title="Snowflake Partner Connect Box" />

    If you’re using the classic version of the Snowflake UI, you can click the **Partner Connect** button in the top bar of your account. From there, click on the dbt tile to open up the connect box. 

    <Lightbox src="/img/snowflake_tutorial/snowflake_classic_ui_partner_connect.png" title="Snowflake Classic UI - Partner Connect" />

2. In the **Connect to dbt** popup, find the **Optional Grant** option and select the **RAW** and **ANALYTICS** databases. This will grant access for your new dbt user role to each selected database. Then, click **Connect**.

    <Lightbox src="/img/snowflake_tutorial/snowflake_classic_ui_connection_box.png" title="Snowflake Classic UI - Connection Box" />

    <Lightbox src="/img/snowflake_tutorial/snowflake_new_ui_connection_box.png" title="Snowflake New UI - Connection Box" />

3. Click **Activate** when a popup appears: 

<Lightbox src="/img/snowflake_tutorial/snowflake_classic_ui_activation_window.png" title="Snowflake Classic UI - Actviation Window" />

<Lightbox src="/img/snowflake_tutorial/snowflake_new_ui_activation_window.png" title="Snowflake New UI - Activation Window" />

4. After the new tab loads, you will see a form. If you already created a <Constant name="dbt" /> account, you will be asked to provide an account name. If you haven't created an account, you will be asked to provide an account name and password.

5. After you have filled out the form and clicked **Complete Registration**, you will be logged into <Constant name="dbt" /> automatically.

6. Click your account name in the left side menu and select **Account settings**, choose the "Partner Connect Trial" project, and select **snowflake** in the overview table. Select **Edit** and update the **Database** field to `analytics` and the **Warehouse** field to `transforming`.

<Lightbox src="/img/snowflake_tutorial/dbt_cloud_snowflake_project_overview.png" title="dbt - Snowflake Project Overview" />

<Lightbox src="/img/snowflake_tutorial/dbt_cloud_update_database_and_warehouse.png" title="dbt - Update Database and Warehouse" />

</TabItem>
<TabItem value="manual-connect" label="Connect manually">


1. Create a new project in <Constant name="dbt" />. Navigate to **Account settings** (by clicking on your account name in the left side menu), and click **+ New Project**.
2. Enter a project name and click **Continue**.
3. In the **Configure your development environment** section, click the **Connection** dropdown menu and select **Add new connection**. This directs you to the connection configuration settings. 
4. In the **Type** section, select **Snowflake**.

    <Lightbox src="/img/snowflake_tutorial/dbt_cloud_setup_snowflake_connection_start.png" title="dbt - Choose Snowflake Connection" />

5. Enter your **Settings** for Snowflake with: 
    * **Account** &mdash; Find your account by using the Snowflake trial account URL and removing `snowflakecomputing.com`. The order of your account information will vary by Snowflake version. For example, Snowflake's Classic console URL might look like: `oq65696.west-us-2.azure.snowflakecomputing.com`. The AppUI or Snowsight URL might look more like: `snowflakecomputing.com/west-us-2.azure/oq65696`. In both examples, your account will be: `oq65696.west-us-2.azure`. For more information, see [Account Identifiers](https://docs.snowflake.com/en/user-guide/admin-account-identifier.html) in the Snowflake docs.  

        <Snippet path="snowflake-acct-name" />
    
    * **Role** &mdash; Leave blank for now. You can update this to a default Snowflake role later.
    * **Database** &mdash; `analytics`.  This tells dbt to create new models in the analytics database.
    * **Warehouse** &mdash; `transforming`. This tells dbt to use the transforming warehouse that was created earlier.

    <Lightbox src="/img/snowflake_tutorial/dbt_cloud_snowflake_account_settings.png" title="dbt - Snowflake Account Settings" />

6. Click **Save**.
7. Set up your personal development credentials by going to **Your profile** > **Credentials**.
8. Select your project that uses the Snowflake connection. 
9. Click the **configure your development environment and add a connection** link. This directs you to a page where you can enter your personal development credentials.
10. Enter your **Development credentials** for Snowflake with: 
    * **Username** &mdash; The username you created for Snowflake. The username is not your email address and is usually your first and last name together in one word. 
    * **Password** &mdash; The password you set when creating your Snowflake account.
    * **Schema** &mdash; You’ll notice that the schema name has been auto-created for you. By convention, this is `dbt_<first-initial><last-name>`. This is the schema connected directly to your development environment, and it's where your models will be built when running dbt within the <Constant name="studio_ide" />.
    * **Target name** &mdash; Leave as the default.
    * **Threads** &mdash; Leave as 4. This is the number of simultaneous connects that <Constant name="dbt" /> will make to build models concurrently.

    <Lightbox src="/img/snowflake_tutorial/dbt_cloud_snowflake_development_credentials.png" title="dbt - Snowflake Development Credentials" />

11. Click **Test connection**. This verifies that <Constant name="dbt" /> can access your Snowflake account.
12. If the test succeeded, click **Save** to complete the configuration. If it failed, you may need to check your Snowflake settings and credentials.

</TabItem>
</Tabs>