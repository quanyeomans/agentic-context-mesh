---
title: "Resources Intro"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

A [resource]({{ $resourceHRef }}) represents the entity producing telemetry as
resource attributes. For example, {{ $aResource }} producing telemetry that is
running in a container on Kubernetes has {{ $aResource }} name, a pod name, a
namespace, and possibly a deployment name. All four of these attributes can be
included in the resource.

In your observability backend, you can use resource information to better
investigate interesting behavior. For example, if your trace or metrics data
indicate latency in your system, you can narrow it down to a specific container,
pod, or Kubernetes deployment.
