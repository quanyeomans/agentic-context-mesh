---
title: "Components"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

The OpenTelemetry Collector is made up of components that handle telemetry data.
Each component has a specific role in the data pipeline.

## Component Types

- **[Receivers](receiver/)** - Collect telemetry data from various sources and
  formats
- **[Processors](processor/)** - Transform, filter, and enrich telemetry data
- **[Exporters](exporter/)** - Send telemetry data to observability backends
- **[Connectors](connector/)** - Connect two pipelines, acting as both exporter
  and receiver
- **[Extensions](extension/)** - Provide additional capabilities like health
  checks
