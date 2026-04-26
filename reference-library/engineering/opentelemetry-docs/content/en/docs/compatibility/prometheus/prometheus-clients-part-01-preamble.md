---
title: "Prometheus Client Libraries vs. OpenTelemetry"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>

> [!NOTE]
>
> This page covers Java and Go. Examples for other languages are planned.

This guide is for developers familiar with the
[Prometheus client libraries](https://prometheus.io/docs/instrumenting/clientlibs/)
who want to understand equivalent patterns in the OpenTelemetry metrics API and
SDK. It covers the most common patterns, but is not exhaustive.