---
title: "Setup Pages Intro"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<li>Maintained by: {props.meta.maintained_by}</li>
    <li>Authors: {props.meta.authors}</li>
    <li>GitHub repo: <a href={`https://github.com/${props.meta.github_repo}`}>{props.meta.github_repo}</a>   <a href={`https://github.com/${props.meta.github_repo}`}></a></li>
    <li>PyPI package: <code>{props.meta.pypi_package}</code> <a href={`https://badge.fury.io/py/${props.meta.pypi_package}`}></a></li>
    <li>Slack channel: <a href={props.meta.slack_channel_link}>{props.meta.slack_channel_name}</a></li>
    <li>Supported dbt Core version: {props.meta.min_core_version} and newer</li>
    <li><Constant name="dbt" /> support: {props.meta.cloud_support}</li>
    <li>Minimum data platform version: {props.meta.min_supported_version}</li>
    

<h2> Installing {props.meta.pypi_package}</h2>

Use `pip` to install the adapter. Before 1.8, installing the adapter would automatically install `dbt-core` and any additional dependencies. Beginning in 1.8, installing an adapter does not automatically install `dbt-core`. This is because adapters and dbt Core versions have been decoupled from each other so we no longer want to overwrite existing dbt-core installations.
Use the following command for installation:

<code>python -m pip install dbt-core {props.meta.pypi_package}</code>

<h2> Configuring {props.meta.pypi_package} </h2>

<p>For {props.meta.platform_name}-specific configuration, please refer to <a href={props.meta.config_page}>{props.meta.platform_name} configs.</a> </p>
