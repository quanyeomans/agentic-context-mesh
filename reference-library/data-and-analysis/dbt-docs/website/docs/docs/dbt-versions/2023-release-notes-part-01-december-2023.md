## December 2023

- <Expandable alt_header='Semantic Layer updates'>

    The dbt Labs team continues to work on adding new features, fixing bugs, and increasing reliability for the dbt Semantic Layer. The following list explains the updates and fixes for December 2023 in more detail. 

    ## Bug fixes

    - Tableau integration &mdash; The dbt Semantic Layer integration with Tableau now supports queries that resolve to a "NOT IN" clause. This applies to using "exclude" in the filtering user interface. Previously it wasn’t supported.
    - `BIGINT` support &mdash; The dbt Semantic Layer can now support `BIGINT` values with precision greater than 18. Previously it would return an error.
    - Memory leak &mdash; Fixed a memory leak in the JDBC API that would previously lead to intermittent errors when querying it.
    - Data conversion support &mdash; Added support for converting various Redshift and Postgres-specific data types. Previously, the driver would throw an error when encountering columns with those types.

    ## Improvements

    - Deprecation &mdash; We deprecated dbt Metrics and the legacy dbt Semantic Layer, both supported on dbt version 1.5 or lower. This change came into effect on December 15th, 2023.
    - Improved dbt converter tool &mdash; The [dbt converter tool](https://github.com/dbt-labs/dbt-converter) can now help automate some of the work in converting from LookML (Looker's modeling language) for those who are migrating. Previously this wasn’t available. 

  </Expandable>

- <Expandable alt_header='External attributes'>

    The extended attributes feature in dbt Cloud is now GA! It allows for an environment level override on any YAML attribute that a dbt adapter accepts in its `profiles.yml`. You can provide a YAML snippet to add or replace any [profile](/docs/local/profiles.yml) value.

    To learn more, refer to [Extended attributes](/docs/dbt-cloud-environments#extended-attributes).

    The **Extended Attributes** text box is available from your environment's settings page: 

    <Lightbox src="/img/docs/dbt-cloud/using-dbt-cloud/extended-attributes.png" width="85%" title="Example of the Extended attributes text box" />

  </Expandable>

- <Expandable alt_header='Legacy semantic layer'>

    dbt Labs has deprecated dbt Metrics and the legacy dbt Semantic Layer, both supported on dbt version 1.5 or lower. This change starts on December 15th, 2023.

    This deprecation means dbt Metrics and the legacy Semantic Layer are no longer supported. We also removed the feature from the dbt Cloud user interface and documentation site.

    ### Why this change?

    The [re-released dbt Semantic Layer](/docs/use-dbt-semantic-layer/dbt-sl), powered by MetricFlow, offers enhanced flexibility, performance, and user experience, marking a significant advancement for the dbt community.

    ### Key changes and impact

    - **Deprecation date** &mdash; The legacy Semantic Layer and dbt Metrics will be officially deprecated on December 15th, 2023.
    - **Replacement** &mdash; [MetricFlow](/docs/build/build-metrics-intro) replaces dbt Metrics for defining semantic logic. The `dbt_metrics` package will no longer be supported post-deprecation.
    - **New feature** &mdash; Exports replaces the materializing data with `metrics.calculate` functionality and will be available in dbt Cloud in December or January.

    ### Breaking changes and recommendations

    - For users on dbt version 1.5 and lower with dbt Metrics and Snowflake proxy:
    - **Impact**: Post-deprecation, queries using the proxy _will not_ run.

    - For users on dbt version 1.5 and lower using dbt Metrics without Snowflake proxy:
    - **Impact**: No immediate disruption, but the package will not receive updates or support after deprecation
    - **Recommendation**: Plan migration to the re-released Semantic Layer for compatibility with dbt version 1.6 and higher.

    ### Engage and support

    - Feedback and community support &mdash; Engage and share feedback with the dbt Labs team and dbt Community slack using channels like [#dbt-cloud-semantic-layer](https://getdbt.slack.com/archives/C046L0VTVR6) and [#dbt-metricflow](https://getdbt.slack.com/archives/C02CCBBBR1D). Or reach out to your dbt Cloud account representative.
    - Resources for upgrading &mdash; Refer to some additional info and resources to help you upgrade your dbt version:
    - [Upgrade version in dbt Cloud](/docs/dbt-versions/upgrade-dbt-version-in-cloud)
    - [Version migration guides](/docs/dbt-versions/core-upgrade)

  </Expandable>