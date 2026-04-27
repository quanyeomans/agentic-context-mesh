## Gauge {#gauge}

A gauge records an instantaneous value that can increase or decrease. Prometheus
uses a single `Gauge` type for all such values, but OpenTelemetry distinguishes
between **additive** and **non-additive** values when choosing the right
instrument:

- **Non-additive** values cannot be meaningfully summed across instances — for
  example, temperature: adding readings from three room sensors doesn't produce
  a useful number. These map to OTel `Gauge` and `ObservableGauge`.
- **Additive** values can be meaningfully summed across instances — for example,
  connected device counts summed across service instances give a useful total.
  These map to OTel `UpDownCounter` and `ObservableUpDownCounter`.

This distinction applies to all gauge patterns: abs, inc and dec, and callback
variants. See the
[instrument selection guide](/docs/specs/otel/metrics/supplementary-guidelines/#instrument-selection)
for a fuller explanation.

### Gauge — abs

Use this pattern for values recorded as an absolute value — such as a
configuration value or a device setpoint. Prometheus `Gauge` maps to the
OpenTelemetry `Gauge` instrument.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusGauge.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Gauge;

public class PrometheusGauge {
  public static void gaugeUsage() {
    Gauge thermostatSetpoint =
        Gauge.builder()
            .name("thermostat_setpoint_celsius")
            .help("Target temperature set on the thermostat")
            .labelNames("zone")
            .register();

    thermostatSetpoint.labelValues("upstairs").set(22.5);
    thermostatSetpoint.labelValues("downstairs").set(20.0);
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelGauge.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleGauge;
import io.opentelemetry.api.metrics.Meter;

public class OtelGauge {
  // Preallocate attribute keys and, when values are static, entire Attributes objects.
  private static final AttributeKey<String> ZONE = AttributeKey.stringKey("zone");
  private static final Attributes UPSTAIRS = Attributes.of(ZONE, "upstairs");
  private static final Attributes DOWNSTAIRS = Attributes.of(ZONE, "downstairs");

  public static void gaugeUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    DoubleGauge thermostatSetpoint =
        meter
            .gaugeBuilder("thermostat.setpoint")
            .setDescription("Target temperature set on the thermostat")
            .setUnit("Cel")
            .build();

    thermostatSetpoint.set(22.5, UPSTAIRS);
    thermostatSetpoint.set(20.0, DOWNSTAIRS);
  }
}
```

Key differences:

- `set(value)` → `set(value, attributes)`. The method name is the same.
- OpenTelemetry distinguishes `LongGauge` (integers, via `.ofLongs()`) from
  `DoubleGauge` (the default). Prometheus uses a single `Gauge` type.
- Preallocate `AttributeKey` instances (always) and `Attributes` objects (when
  values are static) to avoid per-call allocation on the hot path.

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_gauge.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

var thermostatSetpoint = prometheus.NewGaugeVec(prometheus.GaugeOpts{
	Name: "thermostat_setpoint_celsius",
	Help: "Target temperature set on the thermostat",
}, []string{"zone"})

func prometheusGaugeUsage(reg *prometheus.Registry) {
	reg.MustRegister(thermostatSetpoint)

	thermostatSetpoint.WithLabelValues("upstairs").Set(22.5)
	thermostatSetpoint.WithLabelValues("downstairs").Set(20.0)
}
```

OpenTelemetry

<?code-excerpt "otel_gauge.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

// Preallocate attribute options when values are static to avoid per-call allocation.
var (
	zoneUpstairsGaugeOpts   = []metric.RecordOption{metric.WithAttributes(attribute.String("zone", "upstairs"))}
	zoneDownstairsGaugeOpts = []metric.RecordOption{metric.WithAttributes(attribute.String("zone", "downstairs"))}
)

func otelGaugeUsage(ctx context.Context, meter metric.Meter) {
	thermostatSetpoint, err := meter.Float64Gauge("thermostat.setpoint",
		metric.WithDescription("Target temperature set on the thermostat"),
		metric.WithUnit("Cel"))
	if err != nil {
		panic(err)
	}

	thermostatSetpoint.Record(ctx, 22.5, zoneUpstairsGaugeOpts...)
	thermostatSetpoint.Record(ctx, 20.0, zoneDownstairsGaugeOpts...)
}
```

Key differences:

- `Set(value)` → `Record(ctx, value, metric.WithAttributes(...))`.
- In Go, `meter.Float64Gauge` and `meter.Int64Gauge` are separate methods.
  Prometheus uses a single `Gauge` type.

{{% /tab %}} {{< /tabpane >}}

### Callback gauge — abs

Use a callback gauge (an asynchronous gauge in OpenTelemetry) when a
non-additive value is maintained externally — such as a sensor reading — and you
want to observe it at collection time rather than track it yourself.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusGaugeCallback.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.GaugeWithCallback;

public class PrometheusGaugeCallback {
  public static void gaugeCallbackUsage() {
    // Temperature sensors maintain their own readings in firmware.
    // Use a callback gauge to report those values at scrape time without
    // maintaining a separate gauge in application code.
    GaugeWithCallback.builder()
        .name("room_temperature_celsius")
        .help("Current temperature in the room")
        .labelNames("room")
        .callback(
            callback -> {
              callback.call(SmartHomeDevices.livingRoomTemperatureCelsius(), "living_room");
              callback.call(SmartHomeDevices.bedroomTemperatureCelsius(), "bedroom");
            })
        .register();
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelGaugeCallback.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.Meter;

public class OtelGaugeCallback {
  private static final AttributeKey<String> ROOM = AttributeKey.stringKey("room");
  private static final Attributes LIVING_ROOM = Attributes.of(ROOM, "living_room");
  private static final Attributes BEDROOM = Attributes.of(ROOM, "bedroom");

  public static void gaugeCallbackUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    // Temperature sensors maintain their own readings in firmware.
    // Use an asynchronous gauge to report those values when a MetricReader
    // collects metrics, without maintaining separate gauges in application code.
    meter
        .gaugeBuilder("room.temperature")
        .setDescription("Current temperature in the room")
        .setUnit("Cel")
        .buildWithCallback(
            measurement -> {
              measurement.record(SmartHomeDevices.livingRoomTemperatureCelsius(), LIVING_ROOM);
              measurement.record(SmartHomeDevices.bedroomTemperatureCelsius(), BEDROOM);
            });
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_gauge_callback.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

type temperatureCollector struct{ desc *prometheus.Desc }

func newTemperatureCollector() *temperatureCollector {
	return &temperatureCollector{desc: prometheus.NewDesc(
		"room_temperature_celsius",
		"Current temperature in the room",
		[]string{"room"}, nil,
	)}
}

func (c *temperatureCollector) Describe(ch chan<- *prometheus.Desc) { ch <- c.desc }
func (c *temperatureCollector) Collect(ch chan<- prometheus.Metric) {
	ch <- prometheus.MustNewConstMetric(c.desc, prometheus.GaugeValue, livingRoomTemperatureCelsius(), "living_room")
	ch <- prometheus.MustNewConstMetric(c.desc, prometheus.GaugeValue, bedroomTemperatureCelsius(), "bedroom")
}

func prometheusGaugeCallbackUsage(reg *prometheus.Registry) {
	// Temperature sensors maintain their own readings in firmware.
	// Implement prometheus.Collector to report those values at scrape time.
	reg.MustRegister(newTemperatureCollector())
}
```

OpenTelemetry

<?code-excerpt "otel_gauge_callback.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

var (
	roomLivingRoom = attribute.String("room", "living_room")
	roomBedroom    = attribute.String("room", "bedroom")
)

func otelGaugeCallbackUsage(meter metric.Meter) {
	// Temperature sensors maintain their own readings in firmware.
	// Use an observable gauge to report those values when metrics are collected.
	_, err := meter.Float64ObservableGauge("room.temperature",
		metric.WithDescription("Current temperature in the room"),
		metric.WithUnit("Cel"),
		metric.WithFloat64Callback(func(_ context.Context, o metric.Float64Observer) error {
			o.Observe(livingRoomTemperatureCelsius(), metric.WithAttributes(roomLivingRoom))
			o.Observe(bedroomTemperatureCelsius(), metric.WithAttributes(roomBedroom))
			return nil
		}))
	if err != nil {
		panic(err)
	}
}
```

Key differences:

- The Prometheus example implements `prometheus.Collector` with `Describe` and
  `Collect` methods to report labeled gauge values.

{{% /tab %}} {{< /tabpane >}}

### Gauge — inc and dec

Prometheus `Gauge` supports incrementing and decrementing for values that change
gradually — such as the number of connected devices or active sessions.
OpenTelemetry `Gauge` records absolute values only; this pattern maps to the
OpenTelemetry `UpDownCounter` instrument.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusUpDownCounter.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Gauge;

public class PrometheusUpDownCounter {
  public static void upDownCounterUsage() {
    // Prometheus uses Gauge for values that can increase or decrease.
    Gauge devicesConnected =
        Gauge.builder()
            .name("devices_connected")
            .help("Number of smart home devices currently connected")
            .labelNames("device_type")
            .register();

    // Increment when a device connects, decrement when it disconnects.
    devicesConnected.labelValues("thermostat").inc();
    devicesConnected.labelValues("thermostat").inc();
    devicesConnected.labelValues("lock").inc();
    devicesConnected.labelValues("lock").dec();
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelUpDownCounter.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.LongUpDownCounter;
import io.opentelemetry.api.metrics.Meter;

public class OtelUpDownCounter {
  // Preallocate attribute keys and, when values are static, entire Attributes objects.
  private static final AttributeKey<String> DEVICE_TYPE = AttributeKey.stringKey("device_type");
  private static final Attributes THERMOSTAT = Attributes.of(DEVICE_TYPE, "thermostat");
  private static final Attributes LOCK = Attributes.of(DEVICE_TYPE, "lock");

  public static void upDownCounterUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    LongUpDownCounter devicesConnected =
        meter
            .upDownCounterBuilder("devices.connected")
            .setDescription("Number of smart home devices currently connected")
            .build();

    // add() accepts positive and negative values.
    devicesConnected.add(1, THERMOSTAT);
    devicesConnected.add(1, THERMOSTAT);
    devicesConnected.add(1, LOCK);
    devicesConnected.add(-1, LOCK);
  }
}
```

Key differences:

- `inc()` / `dec()` → `add(1)` / `add(-1)`. `add()` accepts both positive and
  negative values.
- The Prometheus type is `Gauge`; the OpenTelemetry type is `LongUpDownCounter`
  (or `DoubleUpDownCounter` via `.ofDoubles()`).

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_up_down_counter.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

// Prometheus uses Gauge for values that can increase or decrease.
var devicesConnected = prometheus.NewGaugeVec(prometheus.GaugeOpts{
	Name: "devices_connected",
	Help: "Number of smart home devices currently connected",
}, []string{"device_type"})

func prometheusUpDownCounterUsage(reg *prometheus.Registry) {
	reg.MustRegister(devicesConnected)

	// Increment when a device connects, decrement when it disconnects.
	devicesConnected.WithLabelValues("thermostat").Inc()
	devicesConnected.WithLabelValues("thermostat").Inc()
	devicesConnected.WithLabelValues("lock").Inc()
	devicesConnected.WithLabelValues("lock").Dec()
}
```

OpenTelemetry

<?code-excerpt "otel_up_down_counter.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

// Preallocate attribute options when values are static to avoid per-call allocation.
var (
	deviceThermostatAddOpts = []metric.AddOption{metric.WithAttributes(attribute.String("device_type", "thermostat"))}
	deviceLockAddOpts       = []metric.AddOption{metric.WithAttributes(attribute.String("device_type", "lock"))}
)

func otelUpDownCounterUsage(ctx context.Context, meter metric.Meter) {
	devicesConnected, err := meter.Int64UpDownCounter("devices.connected",
		metric.WithDescription("Number of smart home devices currently connected"))
	if err != nil {
		panic(err)
	}

	// Add() accepts positive and negative values.
	devicesConnected.Add(ctx, 1, deviceThermostatAddOpts...)
	devicesConnected.Add(ctx, 1, deviceThermostatAddOpts...)
	devicesConnected.Add(ctx, 1, deviceLockAddOpts...)
	devicesConnected.Add(ctx, -1, deviceLockAddOpts...)
}
```

Key differences:

- `Inc()` / `Dec()` → `Add(ctx, 1, ...)` / `Add(ctx, -1, ...)`. `Add()` accepts
  both positive and negative values.
- The Prometheus type is `Gauge`; the OpenTelemetry type is `Int64UpDownCounter`
  (or `Float64UpDownCounter` via `meter.Float64UpDownCounter`).

{{% /tab %}} {{< /tabpane >}}

### Callback gauge — inc and dec

Use a callback gauge (an asynchronous up-down counter in OpenTelemetry) when an
additive count that would otherwise be tracked with `inc()`/`dec()` is
maintained externally — such as by a device manager or connection pool — and you
want to observe it at collection time.

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusUpDownCounterCallback.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.GaugeWithCallback;

public class PrometheusUpDownCounterCallback {
  public static void upDownCounterCallbackUsage() {
    // The device manager maintains the count of connected devices.
    // Use a callback gauge to report that value at scrape time.
    GaugeWithCallback.builder()
        .name("devices_connected")
        .help("Number of smart home devices currently connected")
        .labelNames("device_type")
        .callback(
            callback -> {
              callback.call(SmartHomeDevices.connectedDeviceCount("thermostat"), "thermostat");
              callback.call(SmartHomeDevices.connectedDeviceCount("lock"), "lock");
            })
        .register();
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelUpDownCounterCallback.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.Meter;

public class OtelUpDownCounterCallback {
  private static final AttributeKey<String> DEVICE_TYPE = AttributeKey.stringKey("device_type");
  private static final Attributes THERMOSTAT = Attributes.of(DEVICE_TYPE, "thermostat");
  private static final Attributes LOCK = Attributes.of(DEVICE_TYPE, "lock");

  public static void upDownCounterCallbackUsage(OpenTelemetry openTelemetry) {
    Meter meter = openTelemetry.getMeter("smart.home");
    // The device manager maintains the count of connected devices.
    // Use an asynchronous up-down counter to report that value when a MetricReader
    // collects metrics.
    meter
        .upDownCounterBuilder("devices.connected")
        .setDescription("Number of smart home devices currently connected")
        .buildWithCallback(
            measurement -> {
              measurement.record(SmartHomeDevices.connectedDeviceCount("thermostat"), THERMOSTAT);
              measurement.record(SmartHomeDevices.connectedDeviceCount("lock"), LOCK);
            });
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_up_down_counter_callback.go"?>

```go
package main

import "github.com/prometheus/client_golang/prometheus"

type deviceCountCollector struct{ desc *prometheus.Desc }

func newDeviceCountCollector() *deviceCountCollector {
	return &deviceCountCollector{desc: prometheus.NewDesc(
		"devices_connected",
		"Number of smart home devices currently connected",
		[]string{"device_type"}, nil,
	)}
}

func (c *deviceCountCollector) Describe(ch chan<- *prometheus.Desc) { ch <- c.desc }
func (c *deviceCountCollector) Collect(ch chan<- prometheus.Metric) {
	ch <- prometheus.MustNewConstMetric(c.desc, prometheus.GaugeValue, float64(connectedDeviceCount("thermostat")), "thermostat")
	ch <- prometheus.MustNewConstMetric(c.desc, prometheus.GaugeValue, float64(connectedDeviceCount("lock")), "lock")
}

func prometheusUpDownCounterCallbackUsage(reg *prometheus.Registry) {
	// The device manager maintains the count of connected devices.
	// Implement prometheus.Collector to report those values at scrape time.
	reg.MustRegister(newDeviceCountCollector())
}
```

OpenTelemetry

<?code-excerpt "otel_up_down_counter_callback.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

var (
	deviceThermostat = attribute.String("device_type", "thermostat")
	deviceLock       = attribute.String("device_type", "lock")
)

func otelUpDownCounterCallbackUsage(meter metric.Meter) {
	// The device manager maintains the count of connected devices.
	// Use an observable up-down counter to report that value when metrics are collected.
	_, err := meter.Int64ObservableUpDownCounter("devices.connected",
		metric.WithDescription("Number of smart home devices currently connected"),
		metric.WithInt64Callback(func(_ context.Context, o metric.Int64Observer) error {
			o.Observe(int64(connectedDeviceCount("thermostat")), metric.WithAttributes(deviceThermostat))
			o.Observe(int64(connectedDeviceCount("lock")), metric.WithAttributes(deviceLock))
			return nil
		}))
	if err != nil {
		panic(err)
	}
}
```

Key differences:

- The Prometheus example implements `prometheus.Collector` with `Describe` and
  `Collect` methods to report labeled gauge values.
- `Int64ObservableUpDownCounter` uses `metric.WithInt64Callback`.

{{% /tab %}} {{< /tabpane >}}