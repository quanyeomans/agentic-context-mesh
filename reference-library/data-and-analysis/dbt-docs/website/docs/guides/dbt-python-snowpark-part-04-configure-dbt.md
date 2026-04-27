## Configure dbt

1. We are going to be using [Snowflake Partner Connect](https://docs.snowflake.com/en/user-guide/ecosystem-partner-connect.html) to set up a <Constant name="dbt" /> account. Using this method will allow you to spin up a fully fledged dbt account with your [Snowflake connection](/docs/cloud/connect-data-platform/connect-snowflake), [managed repository](/docs/cloud/git/managed-repository), environments, and credentials already established.
2. Navigate out of your worksheet back by selecting **home**.
3. In Snowsight, confirm that you are using the **ACCOUNTADMIN** role.
4. Navigate to the **Data Products** **> Partner Connect**. Find **dbt** either by using the search bar or navigating the **Data Integration**. Select the **dbt** tile.
    <Lightbox src="/img/guides/dbt-ecosystem/dbt-python-snowpark/4-configure-dbt/1-open-partner-connect.png" width="60%" title="Open Partner Connect"/>
5. You should now see a new window that says **Connect to dbt**. Select **Optional Grant** and add the `FORMULA1` database. This will grant access for your new dbt user role to the FORMULA1 database.
    <Lightbox src="/img/guides/dbt-ecosystem/dbt-python-snowpark/4-configure-dbt/2-partner-connect-optional-grant.png" width="60%" title="Partner Connect Optional Grant"/>

6. Ensure the `FORMULA1` is present in your optional grant before clicking **Connect**.  This will create a dedicated dbt user, database, warehouse, and role for your <Constant name="dbt" /> trial.

    <Lightbox src="/img/guides/dbt-ecosystem/dbt-python-snowpark/4-configure-dbt/3-connect-to-dbt.png" width="60%" title="Connect to dbt"/>

7. When you see the **Your partner account has been created** window, click **Activate**.

8. You should be redirected to a <Constant name="dbt" /> registration page. Fill out the form. Make sure to save the password somewhere for login in the future.

    <Lightbox src="/img/guides/dbt-ecosystem/dbt-python-snowpark/4-configure-dbt/4-dbt-cloud-sign-up.png" title="dbt sign up"/>

9. Select **Complete Registration**. You should now be redirected to your <Constant name="dbt" /> account, complete with a connection to your Snowflake account, a deployment and a development environment, and a sample job.

10. To help you version control your dbt project, we have connected it to a [managed repository](/docs/cloud/git/managed-repository), which means that dbt Labs will be hosting your repository for you. This will give you access to a <Constant name="git" /> workflow without you having to create and host the repository yourself. You will not need to know <Constant name="git" /> for this workshop; <Constant name="dbt" /> will help guide you through the workflow. In the future, when you’re developing your own project, [feel free to use your own repository](/docs/cloud/git/connect-github). This will allow you to learn more about features like [Slim CI](/docs/deploy/continuous-integration) builds after this workshop.