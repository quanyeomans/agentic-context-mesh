---
title: "Databricks setup"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Databricks setup <Lifecycle status='preview' />

You can configure the Databricks adapter by running `dbt init` in your CLI or manually providing the `profiles.yml` file with the fields configured for your authentication type.

The Databricks adapter for Fusion supports the following [authentication methods](#supported-authentication-types):
- Personal access token (for individual users)
- Service Principal token (for service users)
- OAuth

## Databricks configuration details

The <Constant name="fusion_engine" /> `dbt-databricks` adapter is the only supported connection method for Databricks.

`dbt-databricks` can connect to Databricks SQL Warehouses. These warehouses are the recommended way to get started with Databricks.

Refer to the [Databricks docs](https://docs.databricks.com/dev-tools/dbt.html#) for more info on how to obtain the credentials for configuring your profile.

## Configure Fusion

Executing `dbt init` in your CLI will prompt for the following fields:

- **Host:** Databricks instance hostname (excluding the `http` or `https` prefix)
- **HTTP Path:** Path to your SQL server or cluster
- **Schema:** The development/staging/deployment schema for the project
- **Catalog (Optional):** The Databricks catalog containing your schemas and tables


Alternatively, you can manually create the `profiles.yml` file and configure the fields. See examples in [authentication](#supported-authentication-types) section for formatting. If there is an existing `profiles.yml` file, you are given the option to retain the existing fields or overwrite them. 

Next, select your authentication method. Follow the on-screen prompts to provide the required information.

## Supported authentication types

<Tabs>

<TabItem value="Personal access token">

Enter your personal access token (PAT) for the Databricks environment. For more information about obtaining a PAT, refer to the [Databricks documentation](https://docs.databricks.com/aws/en/dev-tools/auth/pat). This is considered a legacy feature by Databricks and OAuth is recommended over PATs.

#### Example personal access token configuration

<File name="profiles.yml">

```yml
default:
  target: dev
  outputs:
    dev:
      type: databricks
      database: TRANSFORMING
      schema: JANE_SMITH
      host: YOUR.HOST.COM
      http_path: YOUR/PATH/HERE
      token: ABC123
      auth_type: databricks_cli
      threads: 16
```

</File>

</TabItem>

<TabItem value="Service Principal token">

Enter your Service Principal token for the Databricks environment. For more information about obtaining a Service Principal token, refer to the [Databricks documentation](https://docs.databricks.com/aws/en/admin/users-groups/service-principals).

#### Example Service Principal token configuration

<File name="profiles.yml">

```yml
default:
  target: dev
  outputs:
    dev:
      type: databricks
      database: TRANSFORMING
      schema: JANE_SMITH
      host: YOUR.HOST.COM
      http_path: YOUR/PATH/HERE
      token: ABC123
      auth_type: databricks_cli
      threads: 16
```

</File>

</TabItem>

<TabItem value="OAuth (Recommended)">

Selecting the OAuth option will create a connection to your Databricks environment and open a web browser so you can complete the authentication. Users will be prompted to re-authenticate with each new dbt session they initiate.

#### Example OAuth configuration

<File name="profiles.yml">

```yml
default:
  target: dev
  outputs:
    dev:
      type: databricks
      database: TRANSFORMING
      schema: JANE_SMITH
      host: YOUR.HOST.COM
      http_path: YOUR/PATH/HERE
      auth_type: oauth
      threads: 16
```
</File>

</TabItem>

</Tabs>

## More information

Find Databricks-specific configuration information in the [Databricks adapter reference guide](/reference/resource-configs/databricks-configs).
