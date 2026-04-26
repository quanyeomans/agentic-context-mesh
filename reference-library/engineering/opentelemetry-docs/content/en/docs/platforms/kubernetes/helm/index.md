---
title: "OpenTelemetry Helm Charts"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

## Introduction

[Helm](https://helm.sh/) is a CLI solution for managing Kubernetes applications.

If you chose to use Helm, you can use
[OpenTelemetry Helm Charts](https://github.com/open-telemetry/opentelemetry-helm-charts)
to manage installs of the [OpenTelemetry Collector](/docs/collector),
[OpenTelemetry Operator](/docs/platforms/kubernetes/operator), and
[OpenTelemetry Demo](/docs/demo).

Add the OpenTelemetry Helm repository with:

```sh
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
```
