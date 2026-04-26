---
title: "Using instrumentation libraries"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

{{% docs/languages/libraries-intro rust %}}

## Use Instrumentation Libraries

Each instrumentation library is a [crate](https://crates.io/).

For example, the
[instrumentation library for Actix Web](https://crates.io/crates/opentelemetry-instrumentation-actix-web)
will automatically create [spans](/docs/concepts/signals/traces/#spans) and
[metrics](/docs/concepts/signals/metrics/) based on the inbound HTTP requests.

For a list of available instrumentation libraries, see the
[registry](/ecosystem/registry/?language=rust&component=instrumentation).
