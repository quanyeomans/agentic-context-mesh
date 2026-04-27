---
title: "invalidate_hard_deletes"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# invalidate_hard_deletes <Lifecycle status="legacy" />


Legacy opt-in configuration to enable invalidating hard deleted records while snapshotting the query.


:::warning This is a legacy config &mdash; Use the [`hard_deletes`](/reference/resource-configs/hard-deletes) config instead.

In <Constant name="dbt" /> release tracks and dbt Core 1.9 and higher, the [`hard_deletes`](/reference/resource-configs/hard-deletes) config replaces the `invalidate_hard_deletes` config for better control over how to handle deleted rows from the source. 

For new snapshots, set the config to `hard_deletes='invalidate'` instead of `invalidate_hard_deletes=true`. For existing snapshots, [arrange an update](/reference/snapshot-configs#snapshot-configuration-migration) of pre-existing tables before enabling this setting. 
:::

<VersionBlock firstVersion="1.9">

<File name='snapshots/<filename>.yml'>

```yaml
snapshots:
  - name: snapshot
    relation: source('my_source', 'my_table')
    [config](/reference/snapshot-configs):
      strategy: timestamp
      invalidate_hard_deletes: true | false
```

</File>


</VersionBlock>

<File name='dbt_project.yml'>

```yml
snapshots:
  [<resource-path>](/reference/resource-configs/resource-path):
    +strategy: timestamp
    +invalidate_hard_deletes: true

```

</File>

## Description
Opt-in feature to enable invalidating hard deleted records while snapshotting the query.


## Default
By default the feature is disabled.

## Example

<VersionBlock firstVersion="1.9">
<File name='snapshots/orders.yml'>

```yaml
snapshots:
  - name: orders_snapshot
    relation: source('jaffle_shop', 'orders')
    config:
      schema: snapshots
      database: analytics
      unique_key: id
      strategy: timestamp
      updated_at: updated_at
      invalidate_hard_deletes: true
  ```
</File>

</VersionBlock>
