## Initialization {#initialization}

The examples below cover the two main deployment patterns: exposing a Prometheus
scrape endpoint and pushing to an OTLP endpoint.

### Expose a Prometheus scrape endpoint

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt "src/main/java/otel/PrometheusScrapeInit.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Counter;
import io.prometheus.metrics.exporter.httpserver.HTTPServer;
import java.io.IOException;

public class PrometheusScrapeInit {
  public static void main(String[] args) throws IOException, InterruptedException {
    // Create a counter and register it with the default PrometheusRegistry.
    Counter doorOpens =
        Counter.builder()
            .name("door_opens_total")
            .help("Total number of times a door has been opened")
            .labelNames("door")
            .register();

    // Start the HTTP server; Prometheus scrapes http://localhost:9464/metrics.
    HTTPServer server = HTTPServer.builder().port(9464).buildAndStart();
    Runtime.getRuntime().addShutdownHook(new Thread(server::close));

    doorOpens.labelValues("front").inc();

    Thread.currentThread().join(); // sleep forever
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelScrapeInit.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.exporter.prometheus.PrometheusHttpServer;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;

public class OtelScrapeInit {
  // Preallocate attribute keys and, when values are static, entire Attributes objects.
  private static final AttributeKey<String> DOOR = AttributeKey.stringKey("door");
  private static final Attributes FRONT_DOOR = Attributes.of(DOOR, "front");

  public static void main(String[] args) throws InterruptedException {
    // Configure the SDK: register a Prometheus reader that serves /metrics.
    OpenTelemetrySdk sdk =
        OpenTelemetrySdk.builder()
            .setMeterProvider(
                SdkMeterProvider.builder()
                    .registerMetricReader(PrometheusHttpServer.builder().setPort(9464).build())
                    .build())
            .build();
    Runtime.getRuntime().addShutdownHook(new Thread(sdk::close));

    // Instrumentation code uses the OpenTelemetry API type, not the SDK type directly.
    OpenTelemetry openTelemetry = sdk;

    // Metrics are served at http://localhost:9464/metrics.
    Meter meter = openTelemetry.getMeter("smart.home");
    LongCounter doorOpens =
        meter
            .counterBuilder("door.opens")
            .setDescription("Total number of times a door has been opened")
            .build();

    doorOpens.add(1, FRONT_DOOR);

    Thread.currentThread().join(); // sleep forever
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

<?code-excerpt "prometheus_scrape_init.go"?>

```go
package main

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	// Create a counter and register it with a custom registry.
	reg := prometheus.NewRegistry()
	doorOpens := prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "door_opens_total",
		Help: "Total number of times a door has been opened",
	}, []string{"door"})
	reg.MustRegister(doorOpens)

	// Prometheus scrapes http://localhost:9464/metrics.
	http.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{}))
	go http.ListenAndServe(":9464", nil) //nolint:errcheck

	doorOpens.WithLabelValues("front").Inc()

	select {} // sleep forever
}
```

OpenTelemetry

<?code-excerpt "otel_scrape_init.go"?>

```go
package main

import (
	"context"
	"net/http"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/prometheus"
	"go.opentelemetry.io/otel/metric"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
)

func main() {
	ctx := context.Background()
	// Configure the SDK: register a Prometheus reader that serves /metrics.
	exporter, err := prometheus.New()
	if err != nil {
		panic(err)
	}
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(exporter))
	defer provider.Shutdown(ctx) //nolint:errcheck

	// Metrics are served at http://localhost:9464/metrics.
	http.Handle("/metrics", promhttp.Handler())
	go http.ListenAndServe(":9464", nil) //nolint:errcheck

	// Instrumentation code uses the API, not the SDK, directly.
	meter := provider.Meter("smart.home")
	doorOpens, err := meter.Int64Counter("door.opens",
		metric.WithDescription("Total number of times a door has been opened"))
	if err != nil {
		panic(err)
	}

	doorOpens.Add(ctx, 1, metric.WithAttributes(attribute.String("door", "front")))

	select {} // sleep forever
}
```

{{% /tab %}} {{< /tabpane >}}

### Push metrics to an OTLP endpoint

{{< tabpane text=true >}} {{% tab Java %}}

Prometheus

<?code-excerpt path-base="examples/java/prometheus-compatibility"?>
<?code-excerpt "src/main/java/otel/PrometheusOtlpInit.java"?>

```java
package otel;

import io.prometheus.metrics.core.metrics.Counter;
import io.prometheus.metrics.exporter.opentelemetry.OpenTelemetryExporter;

public class PrometheusOtlpInit {
  public static void main(String[] args) throws Exception {
    // Create a counter and register it with the default PrometheusRegistry.
    Counter doorOpens =
        Counter.builder()
            .name("door_opens_total")
            .help("Total number of times a door has been opened")
            .labelNames("door")
            .register();

    // Start the OTLP exporter. It reads from the default PrometheusRegistry and
    // pushes metrics to the configured endpoint on a fixed interval.
    OpenTelemetryExporter exporter =
        OpenTelemetryExporter.builder()
            .protocol("http/protobuf")
            .endpoint("http://localhost:4318")
            .intervalSeconds(60)
            .buildAndStart();
    Runtime.getRuntime().addShutdownHook(new Thread(exporter::close));

    doorOpens.labelValues("front").inc();

    Thread.currentThread().join(); // sleep forever
  }
}
```

OpenTelemetry

<?code-excerpt "src/main/java/otel/OtelOtlpInit.java"?>

```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.exporter.otlp.http.metrics.OtlpHttpMetricExporter;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.metrics.export.PeriodicMetricReader;
import java.time.Duration;

public class OtelOtlpInit {
  public static void main(String[] args) throws InterruptedException {
    // Configure the SDK: export metrics over OTLP/HTTP on a fixed interval.
    OpenTelemetrySdk sdk =
        OpenTelemetrySdk.builder()
            .setMeterProvider(
                SdkMeterProvider.builder()
                    .registerMetricReader(
                        PeriodicMetricReader.builder(
                                OtlpHttpMetricExporter.builder()
                                    .setEndpoint("http://localhost:4318")
                                    .build())
                            .setInterval(Duration.ofSeconds(60))
                            .build())
                    .build())
            .build();
    Runtime.getRuntime().addShutdownHook(new Thread(sdk::close));

    // Instrumentation code uses the OpenTelemetry API type, not the SDK type directly.
    OpenTelemetry openTelemetry = sdk;

    Meter meter = openTelemetry.getMeter("smart.home");
    LongCounter doorOpens =
        meter
            .counterBuilder("door.opens")
            .setDescription("Total number of times a door has been opened")
            .build();

    doorOpens.add(1);

    Thread.currentThread().join(); // sleep forever
  }
}
```

{{% /tab %}} {{% tab Go %}}

<?code-excerpt path-base="examples/go/prometheus-compatibility"?>

Prometheus

The Prometheus Go client library does not include an OTLP push exporter.

OpenTelemetry

<?code-excerpt "otel_otlp_init.go"?>

```go
package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/metric"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
)

func main() {
	ctx := context.Background()
	// Configure the SDK: export metrics over OTLP/HTTP on a fixed interval.
	// The endpoint defaults to localhost:4318 and can be configured via
	// the OTEL_EXPORTER_OTLP_ENDPOINT environment variable.
	exporter, err := otlpmetrichttp.New(ctx)
	if err != nil {
		panic(err)
	}
	provider := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(exporter)),
	)
	defer provider.Shutdown(ctx) //nolint:errcheck

	meter := provider.Meter("smart.home")
	doorOpens, err := meter.Int64Counter("door.opens",
		metric.WithDescription("Total number of times a door has been opened"))
	if err != nil {
		panic(err)
	}

	doorOpens.Add(ctx, 1, metric.WithAttributes(attribute.String("door", "front")))

	select {} // sleep forever
}
```

{{% /tab %}} {{< /tabpane >}}