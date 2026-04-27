## Query the Semantic Layer

This page will guide you on how to connect and use the following integrations to query your metrics:

- [Connect and query with Google Sheets](#connect-and-query-with-google-sheets)
- [Connect and query with Hex](#connect-and-query-with-hex)
- [Connect and query with Sigma](#connect-and-query-with-sigma)
  
The <Constant name="semantic_layer" /> enables you to connect and query your metric with various available tools like [PowerBI](/docs/cloud-integrations/semantic-layer/power-bi), [Google Sheets](/docs/cloud-integrations/semantic-layer/gsheets), [Hex](https://learn.hex.tech/docs/connect-to-data/data-connections/dbt-integration#dbt-semantic-layer-integration), [Microsoft Excel](/docs/cloud-integrations/semantic-layer/excel), [Tableau](/docs/cloud-integrations/semantic-layer/tableau), and more. 

Query metrics using other tools such as [first-class integrations](/docs/cloud-integrations/avail-sl-integrations), [<Constant name="semantic_layer" />  APIs](/docs/dbt-cloud-apis/sl-api-overview), and [exports](/docs/use-dbt-semantic-layer/exports) to expose tables of metrics and dimensions in your data platform and create a custom integrations.

 ### Connect and query with Google Sheets


<ConnectQueryAPI/>

### Connect and query with Hex
This section will guide you on how to use the Hex integration to query your metrics using Hex. Select the appropriate tab based on your connection method:

<Tabs>
<TabItem value="partner-connect" label="Query Semantic Layer with Hex" default>

1. Navigate to the [Hex login page](https://app.hex.tech/login). 
2. Sign in or make an account (if you don’t already have one). 
  - You can make Hex free trial accounts with your work email or a .edu email.
3. In the top left corner of your page, click on the **HEX** icon to go to the home page.
4. Then, click the **+ New project** button on the top right.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_new.png" width="50%" title="Click the '+ New project' button on the top right"/>
5. Go to the menu on the left side and select **Data browser**. Then select **Add a data connection**. 
6. Click **Snowflake**. Provide your data connection a name and description. You don't need to your data warehouse credentials to use the <Constant name="semantic_layer" />.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_new_data_connection.png" width="50%" title="Select 'Data browser' and then 'Add a data connection' to connect to Snowflake."/>
7. Under **Integrations**, toggle the dbt switch to the right to enable the dbt integration.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_dbt_toggle.png" width="50%" title="Click on the dbt toggle to enable the integration. "/>

8. Enter the following information:
   * Select your version of dbt as 1.6 or higher
   * Enter your Environment ID 
   * Enter your service or personal token 
   * Make sure to click on the **Use <Constant name="semantic_layer" />** toggle. This way, all queries are routed through dbt.
   * Click **Create connection** in the bottom right corner.
9. Hover over **More** on the menu shown in the following image and select **<Constant name="semantic_layer" />**.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_make_sl_cell.png" width="90%" title="Hover over 'More' on the menu and select 'dbt Semantic Layer'."/>

10. Now, you should be able to query metrics using Hex! Try it yourself: 
    - Create a new cell and pick a metric. 
    - Filter it by one or more dimensions.
    - Create a visualization.

</TabItem>
<TabItem value="manual-connect" label="Getting started with the Semantic Layer workshop">

1. Click on the link provided to you in the workshop’s chat. 
   - Look at the **Pinned message** section of the chat if you don’t see it right away.
2. Enter your email address in the textbox provided. Then, select **SQL and Python** to be taken to Hex’s home screen.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/welcome_to_hex.png" width="70%" title="The 'Welcome to Hex' homepage."/>

3. Then click the purple Hex button in the top left corner.
4. Click the **Collections** button on the menu on the left.
5. Select the **<Constant name="semantic_layer" /> Workshop** collection. 
6. Click the **Getting started with the <Constant name="semantic_layer" />** project collection.

<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_collections.png" width="80%" title="Click 'Collections' to select the 'Semantic Layer Workshop' collection."/>

7. To edit this Hex notebook, click the **Duplicate** button from the project dropdown menu (as displayed in the following image). This creates a new copy of the Hex notebook that you own.

<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_duplicate.png" width="80%" title="Click the 'Duplicate' button from the project dropdown menu to create a Hex notebook copy."/>

8. To make it easier to find, rename your copy of the Hex project to include your name.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_rename.png" width="60%" title="Rename your Hex project to include your name."/>

9. Now, you should be able to query metrics using Hex! Try it yourself with the following example queries:

   - In the first cell, you can see a table of the `order_total` metric over time. Add the `order_count` metric to this table.
   - The second cell shows a line graph of the `order_total` metric over time. Play around with the graph! Try changing the time grain using the **Time unit** drop-down menu.
   - The next table in the notebook, labeled “Example_query_2”, shows the number of customers who have made their first order on a given day. Create a new chart cell. Make a line graph of `first_ordered_at` vs `customers` to see how the number of new customers each day changes over time.
   - Create a new semantic layer cell and pick one or more metrics. Filter your metric(s) by one or more dimensions.

<Lightbox src="/img/docs/dbt-cloud/semantic-layer/hex_make_sl_cell.png" width="90%" title="Query metrics using Hex "/>

</TabItem>
</Tabs>

### Connect and query with Sigma
This section will guide you on how to use the Sigma integration to query your metrics using Sigma. If you already have a Sigma account, simply log in and skip to step 6. Otherwise, you'll be using a Sigma account you'll create with Snowflake Partner Connect. 

1. Go back to your Snowflake account. In the Snowflake UI, click on the home icon in the upper left corner. In the left sidebar, select **Data Products**. Then, select **Partner Connect**. Find the Sigma tile by scrolling or by searching for Sigma in the search bar. Click the tile to connect to Sigma.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-partner-connect.png" width="25%" title="Click the '+ New project' button on the top right"/>

2. Select the Sigma tile from the list. Click the **Optional Grant** dropdown menu. Write **RAW** and **ANALYTICS** in the text box and then click **Connect**. 
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-optional-grant.png" width="60%" title="Click the '+ New project' button on the top right"/>

3. Make up a company name and URL to use. It doesn’t matter what URL you use, as long as it’s unique.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-company-name.png" width="50%" title="Click the '+ New project' button on the top right"/>

4. Enter your name and email address. Choose a password for your account.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-create-profile.png" width="50%" title="Click the '+ New project' button on the top right"/>

5. Great! You now have a Sigma account. Before we get started, go back to Snowlake and open a blank worksheet. Run these lines.
- `grant all privileges on all views in schema analytics.SCHEMA to role pc_sigma_role;`
- `grant all privileges on all tables in schema analytics.SCHEMA to role pc_sigma_role;`

6. Click on your bubble in the top right corner. Click the **Administration** button from the dropdown menu.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-admin.png" width="40%" title="Click the '+ New project' button on the top right"/>

7. Scroll down to the integrations section, then select **Add** next to the dbt integration.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-add-integration.png" width="70%" title="Click the '+ New project' button on the top right"/>

8. In the **dbt Integration** section, fill out the required fields, and then hit save:
- Your dbt [service account token](/docs/dbt-cloud-apis/service-tokens) or [personal access tokens](/docs/dbt-cloud-apis/user-tokens).
- Your access URL of your existing Sigma dbt integration. Use `cloud.getdbt.com` as your access URL.
- Your dbt Environment ID.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-add-info.png" width="50%" title="Click the '+ New project' button on the top right"/>

9. Return to the Sigma home page. Create a new workbook.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-make-workbook.png" width="50%" title="Click the '+ New project' button on the top right"/>

10. Click on **Table**, then click on **SQL**. Select Snowflake `PC_SIGMA_WH` as your data connection.
<Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-sigma-make-table.png" width="50%" title="Click the '+ New project' button on the top right"/>

11. Go ahead and query a working metric in your project! For example, let's say you had a metric that measures various order-related values. Here’s how you would query it:

```sql
select * from
  {{ semantic_layer.query (
    metrics = ['order_total', 'order_count', 'large_orders', 'customers_with_orders', 'avg_order_value', 'pct_of_orders_that_are_large'],
    group_by = 
    [Dimension('metric_time').grain('day') ]
) }}
```