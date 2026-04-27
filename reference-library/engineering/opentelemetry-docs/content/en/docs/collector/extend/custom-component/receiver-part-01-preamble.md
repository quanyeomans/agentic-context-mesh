---
title: "Build a receiver"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

OpenTelemetry defines
[distributed tracing](/docs/concepts/glossary/#distributed-tracing) as:

> Traces that track the progression of a single request, known as a trace, as it
> is handled by services that make up an application. The request may be
> initiated by a user or an application. Distributed tracing is a form of
> tracing that traverses process, network, and security boundaries.

Although distributed traces are defined in an application-centric way, you can
think of them as a timeline for _any_ request that moves through your system.
Each distributed trace shows how long a request took from start to finish and
breaks down the steps taken to complete it.

If your system generates tracing telemetry, you can configure your
[OpenTelemetry Collector](/docs/collector/) with a trace receiver designed to
receive and convert that telemetry. The receiver converts your data from its
original format into the OpenTelemetry trace model so the Collector can process
it.

To implement a trace receiver, you need the following:

- A `Config` implementation so the trace receiver can gather and validate its
  configurations in the Collector config.yaml.

- A `receiver.Factory` implementation so the Collector can properly instantiate
  the trace receiver component.

- A `receiver.Traces` implementation that collects the telemetry, converts it to
  the internal trace representation, and passes the telemetry to the next
  consumer in the pipeline.

This tutorial shows you how to create a trace receiver called `tailtracer` that
simulates a pull operation and generates traces as an outcome of that operation.