---
title: "Record Telemetry with API"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

<?code-excerpt path-base="examples/java/api"?>

The API is a set of classes and interfaces for recording telemetry across key
observability signals. The [SDK](../sdk/) is the built-in reference
implementation of the API, [configured](../configuration/) to process and export
telemetry. This page is a conceptual overview of the API, including
descriptions, links to relevant Javadocs, artifact coordinates, and sample API
usage.

The API consists of the following top-level components:

- [Context](#context-api): A standalone API for propagating context throughout
  an application and across application boundaries, including trace context and
  baggage.
- [TracerProvider](#tracerprovider): The API entry point for traces.
- [MeterProvider](#meterprovider): The API entry point for metrics.
- [LoggerProvider](#loggerprovider): The API entry point for logs.
- [OpenTelemetry](#opentelemetry): A holder for top-level API components (i.e.
  `TracerProvider`, `MeterProvider`, `LoggerProvider`, `ContextPropagators`)
  which is convenient to pass to instrumentation.

The API is designed to support multiple implementations. Two implementations are
provided by OpenTelemetry:

- [SDK](../sdk/) reference implementation. This is the right choice for most
  users.
- [No-op](#no-op-implementation) implementation. A minimalist, zero-dependency
  implementation for instrumentations to use by default when the user doesn't
  install an instance.

The API is designed to be taken as a direct dependency by libraries, frameworks,
and application owners. It comes with
[strong backwards compatibility guarantees](https://github.com/open-telemetry/opentelemetry-java/blob/main/VERSIONING.md#compatibility-requirements),
zero transitive dependencies, and
[supports Java 8+](https://github.com/open-telemetry/opentelemetry-java/blob/main/VERSIONING.md#language-version-compatibility).
Libraries and frameworks should depend only on the API and only call methods
from the API, and instruct applications / end users to add a dependency on the
SDK and install a configured instance.

> [!NOTE] Javadoc
>
> For the Javadoc reference of all OpenTelemetry Java components, see
> [javadoc.io/doc/io.opentelemetry](https://javadoc.io/doc/io.opentelemetry).