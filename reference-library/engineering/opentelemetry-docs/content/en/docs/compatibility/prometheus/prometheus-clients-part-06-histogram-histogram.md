## Histogram {#histogram}

A histogram records the distribution of a set of measurements, tracking the
count of observations, their sum, and the number that fall within configurable
bucket boundaries.

Both Prometheus and OpenTelemetry support classic (explicit-bucket) histograms
and native (base2 exponential) histograms. Prometheus also has a `Summary` type,
which has no direct OTel equivalent — see [Summary](#summary) below.

Prometheus `Histogram` maps to the OpenTelemetry `Histogram` instrument.

### Classic (explicit) histogram

Both systems support classic histograms, where fixed bucket boundaries partition
observations into discrete ranges.

- **Bucket configuration**: Prometheus declares bucket boundaries on the
  instrument itself at creation time. In OpenTelemetry, bucket boundaries are
  set on the instrument as a hint that can be overridden or replaced by views
  configured at the SDK level. This separation keeps instrumentation code
  independent of collection configuration. If no boundaries are specified and no
  view is configured, the SDK uses a default set designed for millisecond-scale
  latency
  (`[0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000]`),
  which is likely wrong for second-scale measurements. Always provide boundaries
  or configure a view when migrating existing histograms.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusHistogram.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Histogram;

public class PrometheusHistogram {
  public static void histogramUsage() {
    Histogram deviceCommandDuration =
        Histogram.builder()
            .name("device_command_duration_seconds")
            .help("Time to receive acknowledgment from a smart home device")
            .labelNames("device_type")
            .classicUpperBounds(0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
            .register();

    deviceCommandDuration.labelValues("thermostat").observe(0.35);
    deviceCommandDuration.labelValues("lock").observe(0.85);
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelHistogram.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.metrics.Meter;
import java.util.List;

public class OtelHistogram {
  // Preallocate attribute keys and, when values are static, entire Attributes objects.
  private static final AttributeKey<String> DEVICE_TYPE = AttributeKey.stringKey("device_type");
  private static final Attributes THERMOSTAT = Attributes.of(DEVICE_TYPE, "thermostat");
  private static final Attributes LOCK = Attributes.of(DEVICE_TYPE, "lock");

  public static void histogramUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    // setExplicitBucketBoundariesAdvice() sets default boundaries as a hint to the SDK.
    // Views configured at the SDK level take precedence over this advice.
    DoubleHistogram deviceCommandDuration =
        meter
            .histogramBuilder("device.command.duration")
            .setDescription("Time to receive acknowledgment from a smart home device")
            .setUnit("s")
            .setExplicitBucketBoundariesAdvice(List.of(0.1, 0.25, 0.5, 1.0, 2.5, 5.0))
            .build();

    deviceCommandDuration.record(0.35, THERMOSTAT);
    deviceCommandDuration.record(0.85, LOCK);
  }
}
```

Key differences:

- `observe(value)` → `record(value, attributes)`.
- OpenTelemetry distinguishes `LongHistogram` (integers, via `.ofLongs()`) from
  `DoubleHistogram` (the default). Prometheus uses a single `Histogram` type.
- Preallocate `AttributeKey` instances (always) and `Attributes` objects (when
  values are static) to avoid per-call allocation on the hot path.
- SDK views can override the boundaries set by
  `setExplicitBucketBoundariesAdvice()`, and can also configure other aspects of
  histogram collection such as attribute filtering, min/max recording, and
  instrument renaming.

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_histogram.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

var deviceCommandDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
	Name:    "device_command_duration_seconds",
	Help:    "Time to receive acknowledgment from a smart home device",
	Buckets: []float64{0.1, 0.25, 0.5, 1.0, 2.5, 5.0},
}, []string{"device_type"})

func prometheusHistogramUsage(reg *prometheus.Registry) {
	reg.MustRegister(deviceCommandDuration)

	deviceCommandDuration.WithLabelValues("thermostat").Observe(0.35)
	deviceCommandDuration.WithLabelValues("lock").Observe(0.85)
}
```

OpenTelemetry

<?code-excerpt "otel_histogram.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

// Preallocate attribute options when values are static to avoid per-call allocation.
var (
	deviceThermostatOpts = []metric.RecordOption{metric.WithAttributes(attribute.String("device_type", "thermostat"))}
	deviceLockOpts       = []metric.RecordOption{metric.WithAttributes(attribute.String("device_type", "lock"))}
)

func otelHistogramUsage(ctx context.Context, meter metric.Meter) {
	// WithExplicitBucketBoundaries sets default boundaries as a hint to the SDK.
	// Views configured at the SDK level take precedence over this hint.
	deviceCommandDuration, err := meter.Float64Histogram("device.command.duration",
		metric.WithDescription("Time to receive acknowledgment from a smart home device"),
		metric.WithUnit("s"),
		metric.WithExplicitBucketBoundaries(0.1, 0.25, 0.5, 1.0, 2.5, 5.0))
	if err != nil {
		panic(err)
	}

	deviceCommandDuration.Record(ctx, 0.35, deviceThermostatOpts...)
	deviceCommandDuration.Record(ctx, 0.85, deviceLockOpts...)
}
```

Key differences:

- `Observe(value)` → `Record(ctx, value, metric.WithAttributes(...))`.
- In Go, `metric.WithExplicitBucketBoundaries(...)` is variadic (not a slice).
  Prometheus uses a `Buckets` field in `HistogramOpts`.
- SDK views can override the boundaries set by `WithExplicitBucketBoundaries()`,
  and can also configure other aspects of histogram collection such as attribute
  filtering, min/max recording, and instrument renaming.

{{% /tab %}} {{< /tabpane >}}

### Native (base2 exponential) histogram

Both systems support native (base2 exponential) histograms, which automatically
adjust bucket boundaries to cover the observed range without requiring manual
configuration.

- **Format selection**: Prometheus instruments can emit classic format only,
  native format only, or both simultaneously — enabling gradual migration
  without instrumentation changes. In OpenTelemetry, format selection is
  configured outside instrumentation code — on the exporter or via a view — so
  instrumentation code requires no changes either way.
- **Instrumentation code**: The OpenTelemetry instrumentation code is identical
  for classic and native histograms. The same `record()` calls produce either
  format depending on how the SDK is configured.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

In Prometheus, the histogram format is controlled at instrument creation time.
The example below uses `.nativeOnly()` to restrict to native format; omitting it
would emit both classic and native formats simultaneously:

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusHistogramNative.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Histogram;

public class PrometheusHistogramNative {
  public static void nativeHistogramUsage() {
    Histogram deviceCommandDuration =
        Histogram.builder()
            .name("device_command_duration_seconds")
            .help("Time to receive acknowledgment from a smart home device")
            .labelNames("device_type")
            .nativeOnly()
            .register();

    deviceCommandDuration.labelValues("thermostat").observe(0.35);
    deviceCommandDuration.labelValues("lock").observe(0.85);
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

In Prometheus, setting `NativeHistogramBucketFactor` enables native histograms
alongside the classic bucket configuration — both formats are reported
simultaneously:

<?code-excerpt "prometheus_histogram_native.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

var nativeDeviceCommandDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
	Name:                        "device_command_duration_seconds",
	Help:                        "Time to receive acknowledgment from a smart home device",
	NativeHistogramBucketFactor: 1.1,
}, []string{"device_type"})

func nativeHistogramUsage(reg *prometheus.Registry) {
	reg.MustRegister(nativeDeviceCommandDuration)

	nativeDeviceCommandDuration.WithLabelValues("thermostat").Observe(0.35)
	nativeDeviceCommandDuration.WithLabelValues("lock").Observe(0.85)
}
```

Key differences:

- `NativeHistogramBucketFactor` must be set to a value greater than 1.0 to
  enable native histograms in Go — it is not optional. Setting it to 0 (the zero
  value) disables native histograms entirely. The value controls the maximum
  ratio between consecutive bucket boundaries; smaller values give finer
  resolution at the cost of more buckets. To approximate the same bucket density
  as the commonly used value of `1.1`, set `MaxScale: 3` on
  `AggregationBase2ExponentialHistogram`.

{{% /tab %}} {{< /tabpane >}}

In OpenTelemetry, the instrumentation code is identical to the classic histogram
case. The base2 exponential format is configured separately, outside the
instrumentation layer.

The preferred approach is to configure it on the metric exporter. This applies
to all histograms exported through that exporter without touching
instrumentation code:

{{< tabpane text=true >}} {{% tab Java %}}

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/OtelHistogramExponentialExporter.java"?>

```java
package otel;

import io.opentelemetry.exporter.otlp.http.metrics.OtlpHttpMetricExporter;
import io.opentelemetry.sdk.metrics.Aggregation;
import io.opentelemetry.sdk.metrics.InstrumentType;
import io.opentelemetry.sdk.metrics.export.DefaultAggregationSelector;

public class OtelHistogramExponentialExporter {
  static OtlpHttpMetricExporter createExporter() {
    // Configure the exporter to use exponential histograms for all histogram instruments.
    // This is the preferred approach — it applies globally without modifying instrumentation code.
    return OtlpHttpMetricExporter.builder()
        .setEndpoint("http://localhost:4318")
        .setDefaultAggregationSelector(
            DefaultAggregationSelector.getDefault()
                .with(InstrumentType.HISTOGRAM, Aggregation.base2ExponentialBucketHistogram()))
        .build();
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>
<?code-excerpt "otel_histogram_exponential_exporter.go" region="createExponentialExporter"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
)

func createExponentialExporter(ctx context.Context) (*otlpmetrichttp.Exporter, error) {
	// Configure the exporter to use exponential histograms for all histogram instruments.
	// This is the preferred approach — it applies globally without modifying instrumentation code.
	return otlpmetrichttp.New(ctx,
		otlpmetrichttp.WithAggregationSelector(func(ik sdkmetric.InstrumentKind) sdkmetric.Aggregation {
			if ik == sdkmetric.InstrumentKindHistogram {
				return sdkmetric.AggregationBase2ExponentialHistogram{}
			}
			return sdkmetric.DefaultAggregationSelector(ik)
		}),
	)
}
```

{{% /tab %}} {{< /tabpane >}}

For more granular control — for example, to use base2 exponential histograms for
specific instruments while keeping explicit buckets for others — configure a
view instead:

{{< tabpane text=true >}} {{% tab Java %}}

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/OtelHistogramExponentialView.java"?>

```java
package otel;

import io.opentelemetry.sdk.metrics.Aggregation;
import io.opentelemetry.sdk.metrics.InstrumentSelector;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.metrics.View;

public class OtelHistogramExponentialView {
  static SdkMeterProvider createMeterProvider() {
    // Use a view for per-instrument control — select a specific instrument by name
    // to use exponential histograms while keeping explicit buckets for others.
    return SdkMeterProvider.builder()
        .registerView(
            InstrumentSelector.builder().setName("device.command.duration").build(),
            View.builder().setAggregation(Aggregation.base2ExponentialBucketHistogram()).build())
        .build();
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>
<?code-excerpt "otel_histogram_exponential.go" region="createExponentialView"?>

```go
func createExponentialView() sdkmetric.View {
	// Use a view for per-instrument control — select a specific instrument by name
	// to use exponential histograms while keeping explicit buckets for others.
	return sdkmetric.NewView(
		sdkmetric.Instrument{Name: "device.command.duration"},
		sdkmetric.Stream{Aggregation: sdkmetric.AggregationBase2ExponentialHistogram{}!},
	)
}
```

{{% /tab %}} {{< /tabpane >}}

### Summary {#summary}

Prometheus `Summary` computes quantiles client-side at scrape time and exposes
them as labeled time series (for example, `{quantile="0.95"}`). OpenTelemetry
has no direct equivalent.

For quantile estimation, a **base2 exponential histogram** is the recommended
replacement: it automatically adjusts bucket boundaries to cover the observed
range, and `histogram_quantile()` in PromQL can compute quantiles with bounded
errors at query time. Unlike `Summary`, the results can be aggregated across
instances. See
[Native (base2 exponential) histogram](#native-base2-exponential-histogram).

If you only need count and sum — not quantiles — a histogram with no explicit
bucket boundaries captures those statistics with minimal overhead. The examples
below show this simpler approach.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusSummary.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Summary;

public class PrometheusSummary {
  public static void summaryUsage() {
    Summary deviceCommandDuration =
        Summary.builder()
            .name("device_command_duration_seconds")
            .help("Time to receive acknowledgment from a smart home device")
            .labelNames("device_type")
            .quantile(0.5, 0.05)
            .quantile(0.95, 0.01)
            .quantile(0.99, 0.001)
            .register();

    deviceCommandDuration.labelValues("thermostat").observe(0.35);
    deviceCommandDuration.labelValues("lock").observe(0.85);
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelHistogramAsSummary.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.metrics.Meter;
import java.util.List;

public class OtelHistogramAsSummary {
  private static final AttributeKey<String> DEVICE_TYPE = AttributeKey.stringKey("device_type");
  private static final Attributes THERMOSTAT = Attributes.of(DEVICE_TYPE, "thermostat");
  private static final Attributes LOCK = Attributes.of(DEVICE_TYPE, "lock");

  public static void summaryReplacement(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    // No explicit bucket boundaries: captures count and sum, a good stand-in for most
    // Summary use cases. For quantile estimation, add boundaries that bracket your thresholds.
    DoubleHistogram deviceCommandDuration =
        meter
            .histogramBuilder("device.command.duration")
            .setDescription("Time to receive acknowledgment from a smart home device")
            .setUnit("s")
            .setExplicitBucketBoundariesAdvice(List.of())
            .build();

    deviceCommandDuration.record(0.35, THERMOSTAT);
    deviceCommandDuration.record(0.85, LOCK);
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_summary.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

var summaryDeviceCommandDuration = prometheus.NewSummaryVec(prometheus.SummaryOpts{
	Name:       "device_command_duration_seconds",
	Help:       "Time to receive acknowledgment from a smart home device",
	Objectives: map[float64]float64{0.5: 0.05, 0.95: 0.01, 0.99: 0.001},
}, []string{"device_type"})

func summaryUsage(reg *prometheus.Registry) {
	reg.MustRegister(summaryDeviceCommandDuration)

	summaryDeviceCommandDuration.WithLabelValues("thermostat").Observe(0.35)
	summaryDeviceCommandDuration.WithLabelValues("lock").Observe(0.85)
}
```

OpenTelemetry

<?code-excerpt "otel_histogram_as_summary.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

// Preallocate attribute options when values are static to avoid per-call allocation.
var (
	summaryThermostatOpts = []metric.RecordOption{metric.WithAttributes(attribute.String("device_type", "thermostat"))}
	summaryLockOpts       = []metric.RecordOption{metric.WithAttributes(attribute.String("device_type", "lock"))}
)

func summaryReplacement(ctx context.Context, meter metric.Meter) {
	// No explicit bucket boundaries: captures count and sum only.
	// For quantile estimation, prefer a base2 exponential histogram instead.
	deviceCommandDuration, err := meter.Float64Histogram("device.command.duration",
		metric.WithDescription("Time to receive acknowledgment from a smart home device"),
		metric.WithUnit("s"),
		metric.WithExplicitBucketBoundaries()) // no boundaries
	if err != nil {
		panic(err)
	}

	deviceCommandDuration.Record(ctx, 0.35, summaryThermostatOpts...)
	deviceCommandDuration.Record(ctx, 0.85, summaryLockOpts...)
}
```

{{% /tab %}} {{< /tabpane >}}