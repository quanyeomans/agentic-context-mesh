## Conceptual differences

Before looking at code, it helps to understand a few structural differences
between the two systems. The
[Prometheus and OpenMetrics Compatibility](/docs/specs/otel/compatibility/prometheus_and_openmetrics/)
specification documents the complete translation rules between the two systems.
This section covers the differences most relevant to writing new instrumentation
code.

### Registry (MeterProvider)

In Prometheus, metrics register to a registry — by default a global one. You can
declare a metric anywhere in your code, and it becomes available for scraping
once registered. The exporter (HTTP server or OTLP push) is attached to the
registry as a separate, independent step.

In OpenTelemetry, `MeterProvider` and `Meter` are part of the metrics API. You
obtain a `Meter` — scoped to your library or component — from a `MeterProvider`,
and create instruments from that `Meter`. How those measurements are processed —
which exporters receive them, how they are aggregated, on what schedule — is
determined by the SDK bound to the `MeterProvider` and its configuration, which
is separate from the instrumentation code itself (see
[API and SDK](#otel-api-and-sdk)).

Like Prometheus, OpenTelemetry supports both a global `MeterProvider` (requiring
no explicit wiring from instrumentation code) and explicit `MeterProvider`
instances that can be passed to libraries which support them.

### Label names (attributes)

Prometheus requires label _names_ to be declared at metric creation time. Label
_values_ are bound at record time, via `labelValues(...)`.

OpenTelemetry has no upfront label declaration. Attribute keys and values are
both provided together at the time of the measurement via `Attributes`.

### Naming conventions

Prometheus uses `snake_case` metric names. Counter names end in `_total`. By
convention, Prometheus metric names are prefixed with the application or library
name to avoid collisions (for example, `smart_home_hvac_on_seconds_total`),
since all metrics share a flat global namespace.

OpenTelemetry conventionally uses
[dotted names](/docs/specs/semconv/general/naming/). Ownership and namespacing
are captured in the instrumentation scope (the `Meter` name, for example
`smart.home`), so metric names themselves do not need a prefix (for example,
`hvac.on`). When exporting to Prometheus, the exporter translates names: dots
become underscores, unit abbreviations expand to full words (for example, `s` →
`seconds`), and counters receive a `_total` suffix. An OpenTelemetry counter
named `hvac.on` with unit `s` is exported as `hvac_on_seconds_total`. See the
[compatibility specification](/docs/specs/otel/compatibility/prometheus_and_openmetrics/)
for the complete set of name translation rules. The translation strategy is
configurable — for example, to preserve UTF-8 characters or suppress unit and
type suffixes. See the
[Prometheus exporter](/docs/specs/otel/metrics/sdk_exporters/prometheus/)
configuration reference for details.

### Stateful and callback instruments

Both systems support two recording modes:

- **Prometheus** distinguishes _stateful_ instruments (`Counter`, `Gauge`),
  which maintain their own accumulated value, from function-based instruments,
  which invoke a callback at scrape time to return the current value. The naming
  varies by client library (`GaugeFunc`/`CounterFunc` in Go;
  `GaugeWithCallback`/`CounterWithCallback` in Java).
- **OpenTelemetry** calls these _synchronous_ (counter, histogram, etc.) and
  _asynchronous_ (observed via a registered callback). The semantics are the
  same.

Note also that Prometheus `Gauge` covers two distinct OTel instrument types:
`Gauge` for non-additive values (such as temperature) and `UpDownCounter` for
additive values that can increase or decrease (such as active connections). See
[Gauge](#gauge) for details.

### OTel: API and SDK

OpenTelemetry separates instrumentation from configuration with a two-layer
design: an **API** package and an **SDK** package. The API defines the
interfaces used to record metrics. The SDK provides the implementation — the
concrete provider, exporters, and processing pipeline.

Instrumentation code should depend only on the API. The SDK is configured once
at application startup and wired to an API reference that gets passed to the
rest of the codebase. This keeps instrumentation library code decoupled from any
specific SDK version and makes it straightforward to swap in a no-op
implementation for testing.

### OTel: Instrumentation scope

Prometheus metrics are global: every metric in a process shares the same flat
namespace, identified only by name and labels.

OpenTelemetry scopes each group of instruments to a `Meter`, identified by a
name and optional version (for example, `smart.home`). When exporting to
Prometheus, the scope name and version are added as `otel_scope_name` and
`otel_scope_version` labels on every metric point. Any additional scope
attributes are also added as labels, named `otel_scope_[attr name]`. These
labels appear automatically and may be unfamiliar to users coming from
Prometheus. They can be suppressed via the exporter's `without_scope_info`
option — see the
[Prometheus exporter](/docs/specs/otel/metrics/sdk_exporters/prometheus/)
configuration reference for details. Note that suppressing scope info is only
safe when each metric name is produced by a single scope. If two scopes emit a
metric with the same name, the scope labels are the only thing distinguishing
them; without those labels, you get duplicate time series with no way to
differentiate their origin, which produces invalid output in Prometheus.

### OTel: Aggregation temporality

Prometheus metrics are always cumulative. OpenTelemetry supports both cumulative
and delta temporality, but the Prometheus exporter enforces cumulative for all
instruments. For developers migrating from Prometheus, this is transparent — the
behavior you already rely on is preserved.

### OTel: Resource attributes

Prometheus identifies scrape targets using `job` and `instance` labels, which
are added by the Prometheus server at scrape time.

OpenTelemetry has a `Resource` — structured metadata attached to all telemetry
from a process, with attributes such as `service.name` and
`service.instance.id`. When exporting to Prometheus, the exporter maps resource
attributes to the `job` and `instance` labels, with any remaining attributes
exposed in a `target_info` metric (`target_info` is an OpenMetrics 1.0
convention — if you currently emit it manually from Prometheus, the OTel
equivalent is to set resource attributes). See the
[compatibility specification](/docs/specs/otel/compatibility/prometheus_and_openmetrics/)
for the exact mapping rules. The `target_info` metric can be suppressed via
`without_target_info`, and specific resource attributes can be promoted to
metric-level labels via `with_resource_constant_labels`. See the
[Prometheus exporter](/docs/specs/otel/metrics/sdk_exporters/prometheus/)
configuration reference for details.