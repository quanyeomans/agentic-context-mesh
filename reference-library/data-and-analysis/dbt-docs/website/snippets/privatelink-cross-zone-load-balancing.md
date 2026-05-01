---
title: "Privatelink Cross Zone Load Balancing"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

:::note Cross-Zone Load Balancing
We highly recommend cross-zone load balancing for your NLB or Target Group; some connections may require it. Cross-zone load balancing may also [improve routing distribution and connection resiliency](https://docs.aws.amazon.com/elasticloadbalancing/latest/userguide/how-elastic-load-balancing-works.html#cross-zone-load-balancing). Note that cross-zone connectivity may incur additional data transfer charges, though this should be minimal for requests from <Constant name="dbt" />.

- [Enabling cross-zone load balancing for a load balancer or target group](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/edit-target-group-attributes.html#target-group-cross-zone)
:::
