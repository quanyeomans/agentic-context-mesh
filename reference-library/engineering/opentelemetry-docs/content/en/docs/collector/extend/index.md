---
title: "Extend the Collector"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

The OpenTelemetry Collector is designed to be extensible. While the core
Collector comes with a wide variety of receivers, processors, and exporters, you
may find that you need to support a custom protocol, process data in a specific
way, or send data to a proprietary backend.

This section guides you through extending the Collector using the
[OpenTelemetry Collector Builder (OCB)](./ocb/) and creating custom components.
