## Counter {#counter}

A counter records monotonically increasing values. Prometheus `Counter` maps to
the OpenTelemetry `Counter` instrument.

- **Unit encoding**: Prometheus encodes the unit in the metric name
  (`hvac_on_seconds_total`). OpenTelemetry separates the name (`hvac.on`) from
  the unit (`s`), and the Prometheus exporter appends the unit suffix
  automatically.

### Counter

The Prometheus `Counter` includes two series-management features that have no
OpenTelemetry equivalent:

- **Series pre-initialization**: Prometheus clients can pre-initialize label
  value combinations so they appear in scrape output with value 0 before any
  recording occurs. OpenTelemetry has no equivalent; data points first appear on
  the first `add()` call.
- **Pre-bound series**: Prometheus clients let you cache the result of
  `labelValues()` to pre-bind to a specific label value combination. Subsequent
  calls go directly to the data point, skipping the internal series lookup.
  OpenTelemetry has no equivalent, though it is
  [under discussion](https://github.com/open-telemetry/opentelemetry-specification/issues/4126).

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusCounter.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Counter;

public class PrometheusCounter {
  public static void counterUsage() {
    Counter hvacOnTime =
        Counter.builder()
            .name("hvac_on_seconds_total")
            .help("Total time the HVAC system has been running, in seconds")
            .labelNames("zone")
            .register();

    // Pre-bind to label value sets: subsequent calls go directly to the data point,
    // skipping the internal series lookup.
    var upstairs = hvacOnTime.labelValues("upstairs");
    var downstairs = hvacOnTime.labelValues("downstairs");

    upstairs.inc(127.5);
    downstairs.inc(3600.0);

    // Pre-initialize zones so they appear in /metrics with value 0 on startup.
    hvacOnTime.initLabelValues("basement");
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelCounter.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleCounter;
import io.opentelemetry.api.metrics.Meter;

public class OtelCounter {
  // Preallocate attribute keys and, when values are static, entire Attributes objects.
  private static final AttributeKey<String> ZONE = AttributeKey.stringKey("zone");
  private static final Attributes UPSTAIRS = Attributes.of(ZONE, "upstairs");
  private static final Attributes DOWNSTAIRS = Attributes.of(ZONE, "downstairs");

  public static void counterUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    // HVAC on-time is fractional — use ofDoubles() to get a DoubleCounter.
    // No upfront label declaration: attributes are provided at record time.
    DoubleCounter hvacOnTime =
        meter
            .counterBuilder("hvac.on")
            .setDescription("Total time the HVAC system has been running")
            .setUnit("s")
            .ofDoubles()
            .build();

    hvacOnTime.add(127.5, UPSTAIRS);
    hvacOnTime.add(3600.0, DOWNSTAIRS);
  }
}
```

Key differences:

- `inc(value)` → `add(value)`. Unlike Prometheus, OpenTelemetry requires an
  explicit value — there is no bare `inc()` shorthand.
- OpenTelemetry distinguishes `LongCounter` (integers, the default) from
  `DoubleCounter` (via `.ofDoubles()`, for fractional values). Prometheus uses a
  single `Counter` type.
- Preallocate `AttributeKey` instances (always) and `Attributes` objects (when
  values are static) to avoid per-call allocation on the hot path.

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_counter.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

var hvacOnTime = prometheus.NewCounterVec(prometheus.CounterOpts{
	Name: "hvac_on_seconds_total",
	Help: "Total time the HVAC system has been running, in seconds",
}, []string{"zone"})

func prometheusCounterUsage(reg *prometheus.Registry) {
	reg.MustRegister(hvacOnTime)

	// Pre-bind to label value sets: subsequent calls avoid the series lookup.
	upstairs := hvacOnTime.WithLabelValues("upstairs")
	downstairs := hvacOnTime.WithLabelValues("downstairs")

	upstairs.Add(127.5)
	downstairs.Add(3600.0)

	// Pre-initialize a series so it appears in /metrics with value 0.
	hvacOnTime.WithLabelValues("basement")
}
```

OpenTelemetry

<?code-excerpt "otel_counter.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

// Preallocate attribute options when values are static to avoid per-call allocation.
var (
	zoneUpstairsOpts   = []metric.AddOption{metric.WithAttributes(attribute.String("zone", "upstairs"))}
	zoneDownstairsOpts = []metric.AddOption{metric.WithAttributes(attribute.String("zone", "downstairs"))}
)

func otelCounterUsage(ctx context.Context, meter metric.Meter) {
	// No upfront label declaration: attributes are provided at record time.
	hvacOnTime, err := meter.Float64Counter("hvac.on",
		metric.WithDescription("Total time the HVAC system has been running"),
		metric.WithUnit("s"))
	if err != nil {
		panic(err)
	}

	hvacOnTime.Add(ctx, 127.5, zoneUpstairsOpts...)
	hvacOnTime.Add(ctx, 3600.0, zoneDownstairsOpts...)
}
```

Key differences:

- `Add(value)` → `Add(ctx, value, metric.WithAttributes(...))`. All instrument
  calls require a `context.Context` as the first argument.
- In Go, `meter.Float64Counter` and `meter.Int64Counter` are separate methods.
  Prometheus uses a single `Counter` type.
- Instrument creation returns `(Instrument, error)` and the error must be
  handled.

{{% /tab %}} {{< /tabpane >}}

### Callback (async) counter

Use a callback counter (an asynchronous counter in OpenTelemetry) when the total
is maintained by an external source — such as a device or runtime — and you want
to observe it at collection time rather than increment it yourself.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusCounterCallback.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.CounterWithCallback;

public class PrometheusCounterCallback {
  public static void counterCallbackUsage() {
    // Each zone has its own smart energy meter tracking cumulative joule totals.
    // Use a callback counter to report those values at scrape time without
    // maintaining separate counters in application code.
    CounterWithCallback.builder()
        .name("energy_consumed_joules_total")
        .help("Total energy consumed in joules")
        .labelNames("zone")
        .callback(
            callback -> {
              callback.call(SmartHomeDevices.totalEnergyJoules("upstairs"), "upstairs");
              callback.call(SmartHomeDevices.totalEnergyJoules("downstairs"), "downstairs");
            })
        .register();
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelCounterCallback.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.Meter;

public class OtelCounterCallback {
  private static final AttributeKey<String> ZONE = AttributeKey.stringKey("zone");
  private static final Attributes UPSTAIRS = Attributes.of(ZONE, "upstairs");
  private static final Attributes DOWNSTAIRS = Attributes.of(ZONE, "downstairs");

  public static void counterCallbackUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    // Each zone has its own smart energy meter tracking cumulative joule totals.
    // Use an asynchronous counter to report those values when a MetricReader
    // collects metrics, without maintaining separate counters in application code.
    meter
        .counterBuilder("energy.consumed")
        .setDescription("Total energy consumed")
        .setUnit("J")
        .ofDoubles()
        .buildWithCallback(
            measurement -> {
              measurement.record(SmartHomeDevices.totalEnergyJoules("upstairs"), UPSTAIRS);
              measurement.record(SmartHomeDevices.totalEnergyJoules("downstairs"), DOWNSTAIRS);
            });
  }
}
```

Key differences:

- OpenTelemetry distinguishes integer and floating-point counters;
  `.ofDoubles()` selects the floating-point variant. Prometheus
  `CounterWithCallback` always uses floating-point values.

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_counter_callback.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

type energyCollector struct{ desc *prometheus.Desc }

func newEnergyCollector() *energyCollector {
	return &energyCollector{desc: prometheus.NewDesc(
		"energy_consumed_joules_total",
		"Total energy consumed in joules",
		[]string{"zone"}, nil,
	)}
}

func (c *energyCollector) Describe(ch chan<- *prometheus.Desc) { ch <- c.desc }
func (c *energyCollector) Collect(ch chan<- prometheus.Metric) {
	ch <- prometheus.MustNewConstMetric(c.desc, prometheus.CounterValue, totalEnergyJoules("upstairs"), "upstairs")
	ch <- prometheus.MustNewConstMetric(c.desc, prometheus.CounterValue, totalEnergyJoules("downstairs"), "downstairs")
}

func prometheusCounterCallbackUsage(reg *prometheus.Registry) {
	// Each zone has its own smart energy meter tracking cumulative joule totals.
	// Implement prometheus.Collector to report those values at scrape time.
	reg.MustRegister(newEnergyCollector())
}
```

OpenTelemetry

<?code-excerpt "otel_counter_callback.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

var (
	zoneUpstairs   = attribute.String("zone", "upstairs")
	zoneDownstairs = attribute.String("zone", "downstairs")
)

func otelCounterCallbackUsage(meter metric.Meter) {
	// Each zone has its own smart energy meter tracking cumulative joule totals.
	// Use an observable counter to report those values when metrics are collected.
	_, err := meter.Float64ObservableCounter("energy.consumed",
		metric.WithDescription("Total energy consumed"),
		metric.WithUnit("J"),
		metric.WithFloat64Callback(func(_ context.Context, o metric.Float64Observer) error {
			o.Observe(totalEnergyJoules("upstairs"), metric.WithAttributes(zoneUpstairs))
			o.Observe(totalEnergyJoules("downstairs"), metric.WithAttributes(zoneDownstairs))
			return nil
		}))
	if err != nil {
		panic(err)
	}
}
```

Key differences:

- The Prometheus example implements `prometheus.Collector` with `Describe` and
  `Collect` methods to report labeled counter values.
- OpenTelemetry distinguishes `Float64ObservableCounter` from
  `Int64ObservableCounter`.

{{% /tab %}} {{< /tabpane >}}