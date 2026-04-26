---
title: "Query Builder functions"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<p>When querying your data with {props.type}:</p>


  <li>It returns the data to the cell you clicked on. </li>
  <li> {props.bullet_1}</li>
  <li>{props.bullet_2}</li>
  <li>{props.bullet_3}</li>


## Query Builder functions

<p>The {props.type} Query Builder custom menu has the following capabilities:</p>

<table>
  <thead>
    <tr>
      <th>Menu items</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Metrics</td>
      <td>Search and select metrics.</td>
    </tr>
    <tr>
      <td>Group By</td>
      <td>Search and select dimensions or entities to group by. Dimensions are grouped by the entity of the semantic model they come from. You may choose dimensions on their own without metrics.</td>
    </tr>
    <tr>
      <td>Time Range</td>
      <td>Quickly select time ranges to look at the data, which applies to the main time series for the metrics (metric time), or do more advanced filter using the "Custom" selection.</td>
    </tr>
    <tr>
      <td>Where</td>
      <td>Filter your data. This includes categorical and time filters.</td>
    </tr>
    <tr>
      <td>Order By</td>
      <td>Return your data order.</td>
    </tr>
    <tr>
      <td>Limit</td>
      <td>Set a limit for the rows of your output.</td>
    </tr>
  </tbody>
</table>

<p>Note: Click the **info** button next to any metric or dimension to see its defined description from your dbt project.</p>

#### Modifying time granularity

import SlCustomGranularity from '/snippets/_sl-custom-granularity.md';

<SlCustomGranularity />

#### Filtering data

<p>To use the filter functionality, choose the [dimension](/docs/build/dimensions) you want to filter by and select the operation you want to filter on.</p>


  <li>For categorical dimensions, you can type a value into search or select from a populated list.</li>
  <li>For entities, you must type the value you are looking for as we do not load all of them given the large number of values.</li>
  <li>Continue adding additional filters as needed with AND and OR.</li>
  <li>For time dimensions, you can use the time range selector to filter on presets or custom options. The time range selector applies only to the primary time dimension (<code>metric_time</code>). For all other time dimensions that aren't <code>metric_time</code>, you can use the "Where" option to apply filters.</li>


#### Other settings

<p>If you would like to just query the data values without the headers, you can optionally select the Exclude column names box.</p>
<p>To return your results and keep any previously selected data below it intact, un-select the Clear trailing rows box. By default, we'll clear all trailing rows if there's stale data.</p>

<Lightbox src={ props.queryBuilder } width="35%" title="Run a query in the Query Builder. Use the arrow next to the Query button to select additional settings." />

## Using saved selections

<p>Saved selections allow you to save the inputs you've created in the {props.type} Query Builder and easily access them again so you don't have to continuously build common queries from scratch. To create a saved selection:</p>

<ol>
  <li>Run a query in the Query Builder.</li>
  <li>Save the selection by selecting the arrow next to the Query button and then select Query & Save Selection.</li>
  <li>The application saves these selections, allowing you to view and edit them from the hamburger menu under Saved Selections.</li>
</ol>

<p>{props.PrivateSelections}</p>

### Refreshing selections

<p>Set your saved selections to automatically refresh every time you load the addon. You can do this by selecting Refresh on Load when creating the saved selection. When you access the addon and have saved selections that should refresh, you'll see "Loading..." in the cells that are refreshing.</p>

<p>Public saved selections will refresh for anyone who edits the sheet.</p>

:::tip What's the difference between saved selections and saved queries?

- Saved selections are saved components that you can create only when using the application.
- Saved queries, explained in the next section, are code-defined sections of data you create in your dbt project that you can easily access and use for building selections. You can also use the results from a saved query to create a saved selection.
:::

## Using saved queries

<p>Access [saved queries](/docs/build/saved-queries), powered by MetricFlow, in {props.type} to quickly get results from pre-defined sets of data. To access the saved queries in {props.type}:</p>

<ol>
  <li>Open the hamburger menu in {props.type}.</li>
  <li>Navigate to Saved Queries to access the ones available to you.</li>
  <li>You can also select Build Selection, which allows you to explore the existing query. This won't change the original query defined in the code.</li>
    
      <li>If you use a <code>WHERE</code> filter in a saved query, {props.type} displays the advanced syntax for this filter.</li>
    
</ol>
