---
title: "OpenTelemetry Operator for Kubernetes"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

## Introduction

The
[OpenTelemetry Operator](https://github.com/open-telemetry/opentelemetry-operator)
is an implementation of a
[Kubernetes Operator](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/).

The operator manages:

- [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector)
- [auto-instrumentation of the workloads using OpenTelemetry instrumentation libraries](https://github.com/open-telemetry/opentelemetry-operator#opentelemetry-auto-instrumentation-injection)

## Getting started

To install the operator in an existing cluster, make sure you have
[`cert-manager`](https://cert-manager.io/docs/installation/) installed and run:

```bash
kubectl apply -f https://github.com/open-telemetry/opentelemetry-operator/releases/latest/download/opentelemetry-operator.yaml
```

Once the `opentelemetry-operator` deployment is ready, create an OpenTelemetry
Collector (otelcol) instance, like:

```console
$ kubectl apply -f - <<EOF
apiVersion: opentelemetry.io/v1beta1
kind: OpenTelemetryCollector
metadata:
  name: simplest
spec:
  config:
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318
    processors:
      memory_limiter:
        check_interval: 1s
        limit_percentage: 75
        spike_limit_percentage: 15

    exporters:
      # NOTE: Prior to v0.86.0 use `logging` instead of `debug`.
      debug: {}

    service:
      pipelines:
        traces:
          receivers: [otlp]
          processors: [memory_limiter]
          exporters: [debug]
EOF
```

> [!NOTE]
>
> By default, `opentelemetry-operator` uses the
> [`opentelemetry-collector` image](https://github.com/open-telemetry/opentelemetry-collector-releases/pkgs/container/opentelemetry-collector-releases%2Fopentelemetry-collector).
> When the operator is installed using
> [Helm charts](/docs/platforms/kubernetes/helm/), the
> [`opentelemetry-collector-k8s` image](https://github.com/open-telemetry/opentelemetry-collector-releases/pkgs/container/opentelemetry-collector-releases%2Fopentelemetry-collector-k8s)
> is used. If you need a component not found in these releases, you may need to
> [build your own collector](/docs/collector/extend/ocb/).

For more configuration options and for setting up the injection of
auto-instrumentation of the workloads using OpenTelemetry instrumentation
libraries, see
[OpenTelemetry Operator for Kubernetes](https://github.com/open-telemetry/opentelemetry-operator/blob/main/README.md).
