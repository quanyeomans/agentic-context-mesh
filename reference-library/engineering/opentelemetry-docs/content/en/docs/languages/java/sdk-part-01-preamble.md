---
title: "Manage Telemetry with SDK"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

<?code-excerpt path-base="examples/java/configuration"?>

The SDK is the built-in reference implementation of the [API](../api/),
processing and exporting telemetry produced by instrumentation API calls. This
page is a conceptual overview of the SDK, including descriptions, links to
relevant Javadocs, artifact coordinates, sample programmatic configurations and
more. See **[Configure the SDK](../configuration/)** for details on SDK
configuration, including
[zero-code SDK autoconfigure](../configuration/#zero-code-sdk-autoconfigure).

The SDK consists of the following top level components:

- [SdkTracerProvider](#sdktracerprovider): The SDK implementation of
  `TracerProvider`, including tools for sampling, processing, and exporting
  spans.
- [SdkMeterProvider](#sdkmeterprovider): The SDK implementation of
  `MeterProvider`, including tools for configuring metric streams and reading /
  exporting metrics.
- [SdkLoggerProvider](#sdkloggerprovider): The SDK implementation of
  `LoggerProvider`, including tools for processing and exporting logs.
- [TextMapPropagator](#textmappropagator): Propagates context across process
  boundaries.

These are combined into [OpenTelemetrySdk](#opentelemetrysdk), a carrier object
which makes it convenient to pass fully-configured
[SDK components](#sdk-components) to instrumentation.

The SDK comes packaged with a variety of built-in components which are
sufficient for many use cases, and supports
[plugin interfaces](#sdk-plugin-extension-interfaces) for extensibility.