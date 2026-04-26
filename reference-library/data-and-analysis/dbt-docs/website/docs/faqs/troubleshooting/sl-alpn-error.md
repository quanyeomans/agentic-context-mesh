---
title: "I'm receiving an `Failed ALPN` error when trying to connect to the dbt Semantic Layer."
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

If you're receiving a `Failed ALPN` error when trying to connect the dbt Semantic Layer with the various [data integration tools](/docs/cloud-integrations/avail-sl-integrations) (such as Tableau, DBeaver, Datagrip, ADBC, or JDBC), it typically happens when connecting from a computer behind a corporate VPN or Proxy (like Zscaler or Check Point). 

The root cause is typically the proxy interfering with the TLS handshake as the <Constant name="semantic_layer" /> uses gRPC/HTTP2 for connectivity. To resolve this:

- If your proxy supports gRPC/HTTP2 but isn't configured to allow ALPN, adjust its settings accordingly to allow ALPN. Or create an exception for the <Constant name="dbt" /> domain.
- If your proxy does not support gRPC/HTTP2, add an SSL interception exception for the <Constant name="dbt" /> domain in your proxy settings

This should help in successfully establishing the connection without the Failed ALPN error.
