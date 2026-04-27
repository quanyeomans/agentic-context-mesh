## OpenTelemetry API

The `io.opentelemetry:opentelemetry-api:{{% param vers.otel %}}` artifact
contains the OpenTelemetry API, including traces, metrics, logs, no-op
implementation, baggage, key `TextMapPropagator` implementations, and a
dependency on the [context API](#context-api).

### Providers and Scopes

Providers and scopes are recurring concepts in the OpenTelemetry API. A scope is
a logical unit within the application which telemetry is associated with. A
provider provides components for recording telemetry relative to a particular
scope:

- [TracerProvider](#tracerprovider) provides scoped [Tracers](#tracer) for
  recording spans.
- [MeterProvider](#meterprovider) provides scoped [Meters](#meter) for recording
  metrics.
- [LoggerProvider](#loggerprovider) provides scoped [Loggers](#logger) for
  recording logs.

> [!WARNING]
>
> {{% param logBridgeWarning %}}

A scope is identified by the triplet (name, version, schemaUrl). Care must be
taken to ensure the scope identity is unique. A typical approach is to set the
scope name to the package name or fully qualified class name, and to set the
scope version to the library version. If emitting telemetry for multiple signals
(i.e. metrics and traces), the same scope should be used. See
[instrumentation scope](/docs/concepts/instrumentation-scope/) for details.

The following code snippet explores provider and scope API usage:


<?code-excerpt "src/main/java/otel/ProvidersAndScopes.java"?>
```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.logs.Logger;
import io.opentelemetry.api.logs.LoggerProvider;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.metrics.MeterProvider;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.trace.TracerProvider;

public class ProvidersAndScopes {

  private static final String SCOPE_NAME = "fully.qualified.name";
  private static final String SCOPE_VERSION = "1.0.0";
  private static final String SCOPE_SCHEMA_URL = "https://example";

  public static void providersUsage(OpenTelemetry openTelemetry) {
    // Access providers from an OpenTelemetry instance
    TracerProvider tracerProvider = openTelemetry.getTracerProvider();
    MeterProvider meterProvider = openTelemetry.getMeterProvider();
    // NOTE: LoggerProvider is a special case and should only be used to bridge logs from other
    // logging APIs / frameworks into OpenTelemetry.
    LoggerProvider loggerProvider = openTelemetry.getLogsBridge();

    // Access tracer, meter, logger from providers to record telemetry for a particular scope
    Tracer tracer =
        tracerProvider
            .tracerBuilder(SCOPE_NAME)
            .setInstrumentationVersion(SCOPE_VERSION)
            .setSchemaUrl(SCOPE_SCHEMA_URL)
            .build();
    Meter meter =
        meterProvider
            .meterBuilder(SCOPE_NAME)
            .setInstrumentationVersion(SCOPE_VERSION)
            .setSchemaUrl(SCOPE_SCHEMA_URL)
            .build();
    Logger logger =
        loggerProvider
            .loggerBuilder(SCOPE_NAME)
            .setInstrumentationVersion(SCOPE_VERSION)
            .setSchemaUrl(SCOPE_SCHEMA_URL)
            .build();

    // ...optionally, shorthand versions are available if scope version and schemaUrl aren't
    // available
    tracer = tracerProvider.get(SCOPE_NAME);
    meter = meterProvider.get(SCOPE_NAME);
    logger = loggerProvider.get(SCOPE_NAME);
  }
}
```


### Attributes

[Attributes](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/common/Attributes.html)
is a bundle of key value pairs representing the
[attribute definition](/docs/specs/otel/common/#attribute). `Attributes` are a
recurring concept in the OpenTelemetry API:

- [Spans](#span), span events, and span links have attributes.
- The measurements recorded to [metric instruments](#meter) have attributes.
- [LogRecords](#logrecordbuilder) have attributes.

See [semantic attributes](#semantic-attributes) for attribute constants
generated from the semantic conventions.

See [attribute naming](/docs/specs/semconv/general/naming/) for guidance on
attribute naming.

The following code snippet explores `Attributes` API usage:


<?code-excerpt "src/main/java/otel/AttributesUsage.java"?>
```java
package otel;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.common.AttributesBuilder;
import java.util.Map;

public class AttributesUsage {
  // Establish static constant for attribute keys and reuse to avoid allocations
  private static final AttributeKey<String> SHOP_ID = AttributeKey.stringKey("com.acme.shop.id");
  private static final AttributeKey<String> SHOP_NAME =
      AttributeKey.stringKey("com.acme.shop.name");
  private static final AttributeKey<Long> CUSTOMER_ID =
      AttributeKey.longKey("com.acme.customer.id");
  private static final AttributeKey<String> CUSTOMER_NAME =
      AttributeKey.stringKey("com.acme.customer.name");

  public static void attributesUsage() {
    // Use a varargs initializer and pre-allocated attribute keys. This is the most efficient way to
    // create attributes.
    Attributes attributes =
        Attributes.of(
            SHOP_ID,
            "abc123",
            SHOP_NAME,
            "opentelemetry-demo",
            CUSTOMER_ID,
            123L,
            CUSTOMER_NAME,
            "Jack");

    // ...or use a builder.
    attributes =
        Attributes.builder()
            .put(SHOP_ID, "abc123")
            .put(SHOP_NAME, "opentelemetry-demo")
            .put(CUSTOMER_ID, 123)
            .put(CUSTOMER_NAME, "Jack")
            // Optionally initialize attribute keys on the fly
            .put(AttributeKey.stringKey("com.acme.string-key"), "value")
            .put(AttributeKey.booleanKey("com.acme.bool-key"), true)
            .put(AttributeKey.longKey("com.acme.long-key"), 1L)
            .put(AttributeKey.doubleKey("com.acme.double-key"), 1.1)
            .put(AttributeKey.stringArrayKey("com.acme.string-array-key"), "value1", "value2")
            .put(AttributeKey.booleanArrayKey("come.acme.bool-array-key"), true, false)
            .put(AttributeKey.longArrayKey("come.acme.long-array-key"), 1L, 2L)
            .put(AttributeKey.doubleArrayKey("come.acme.double-array-key"), 1.1, 2.2)
            // Optionally omit initializing AttributeKey
            .put("com.acme.string-key", "value")
            .put("com.acme.bool-key", true)
            .put("come.acme.long-key", 1L)
            .put("come.acme.double-key", 1.1)
            .put("come.acme.string-array-key", "value1", "value2")
            .put("come.acme.bool-array-key", true, false)
            .put("come.acme.long-array-key", 1L, 2L)
            .put("come.acme.double-array-key", 1.1, 2.2)
            .build();

    // Attributes has a variety of methods for manipulating and reading data.
    // Read an attribute key:
    String shopIdValue = attributes.get(SHOP_ID);
    // Inspect size:
    int size = attributes.size();
    boolean isEmpty = attributes.isEmpty();
    // Convert to a map representation:
    Map<AttributeKey<?>, Object> map = attributes.asMap();
    // Iterate through entries, printing each to the template: <key> (<type>): <value>\n
    attributes.forEach(
        (attributeKey, value) ->
            System.out.printf(
                "%s (%s): %s%n", attributeKey.getKey(), attributeKey.getType(), value));
    // Convert to a builder, remove the com.acme.customer.id and any entry whose key starts with
    // com.acme.shop, and build a new instance:
    AttributesBuilder builder = attributes.toBuilder();
    builder.remove(CUSTOMER_ID);
    builder.removeIf(attributeKey -> attributeKey.getKey().startsWith("com.acme.shop"));
    Attributes trimmedAttributes = builder.build();
  }
}
```


### OpenTelemetry

> [!NOTE] Spring Boot Starter
>
> The Spring Boot starter is a special case where `OpenTelemetry` is available
> as a Spring bean. Simply inject `OpenTelemetry` into your Spring components.
>
> Read more about
> [extending the Spring Boot starter with custom manual instrumentation](/docs/zero-code/java/spring-boot-starter/api/).

[OpenTelemetry](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/OpenTelemetry.html)
is a holder for top-level API components which is convenient to pass to
instrumentation.

`OpenTelemetry` consists of:

- [TracerProvider](#tracerprovider): The API entry point for traces.
- [MeterProvider](#meterprovider): The API entry point for metrics.
- [LoggerProvider](#loggerprovider): The API entry point for logs.
- [ContextPropagators](#contextpropagators): The API entry point for context
  propagation.

The following code snippet explores `OpenTelemetry` API usage:


<?code-excerpt "src/main/java/otel/OpenTelemetryUsage.java"?>
```java
package otel;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.logs.LoggerProvider;
import io.opentelemetry.api.metrics.MeterProvider;
import io.opentelemetry.api.trace.TracerProvider;
import io.opentelemetry.context.propagation.ContextPropagators;

public class OpenTelemetryUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void openTelemetryUsage(OpenTelemetry openTelemetry) {
    // Access TracerProvider, MeterProvider, LoggerProvider, ContextPropagators
    TracerProvider tracerProvider = openTelemetry.getTracerProvider();
    MeterProvider meterProvider = openTelemetry.getMeterProvider();
    LoggerProvider loggerProvider = openTelemetry.getLogsBridge();
    ContextPropagators propagators = openTelemetry.getPropagators();
  }
}
```


### GlobalOpenTelemetry

> [!NOTE] Java agent
>
> The Java agent is a special case where `GlobalOpenTelemetry` is set by the
> agent. Simply call `GlobalOpenTelemetry.getOrNoop()` to access the
> `OpenTelemetry` instance.
>
> Read more about
> [extending the Java agent with custom manual instrumentation](/docs/zero-code/java/agent/api/).

[GlobalOpenTelemetry](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/GlobalOpenTelemetry.html)
holds a global singleton [OpenTelemetry](#opentelemetry) instance.

`GlobalOpenTelemetry` is designed in a very particular way to avoid
initialization ordering issues, and as a result should be used judiciously.
Specifically, `GlobalOpenTelemetry.get()` always returns the same result,
regardless of whether `GlobalOpenTelemetry.set(..)` has been called. Internally,
if `get()` is called prior to `set()`, the implementation internally calls
`set(..)` with a [no-op implementation](#no-op-implementation) and returns it.
Because `set(..)` triggers an exception if called more than once, calling
`set(..)` after `get()` causes an exception rather than silently failing.

The Java agent represents a special case: `GlobalOpenTelemetry` is the only
mechanism for
[native instrumentation](../instrumentation/#native-instrumentation) and
[manual instrumentation](../instrumentation/#manual-instrumentation) to record
telemetry to the `OpenTelemetry` instance installed by the agent. Using this
instance is important and useful, and we recommend accessing
`GlobalOpenTelemetry` as follows:

**For native instrumentation, default to `GlobalOpenTelemetry.getOrNoop()`:**


<?code-excerpt "src/main/java/otel/GlobalOpenTelemetryNativeInstrumentationUsage.java"?>
```java
package otel;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.OpenTelemetry;

public class GlobalOpenTelemetryNativeInstrumentationUsage {

  public static void globalOpenTelemetryUsage(OpenTelemetry openTelemetry) {
    // Initialized with OpenTelemetry from java agent if present, otherwise no-op implementation.
    MyClient client1 = new MyClientBuilder().build();

    // Initialized with an explicit OpenTelemetry instance, overriding the java agent instance.
    MyClient client2 = new MyClientBuilder().setOpenTelemetry(openTelemetry).build();
  }

  /**
   * An example library with native OpenTelemetry instrumentation, initialized via {@link
   * MyClientBuilder}.
   */
  public static class MyClient {
    private final OpenTelemetry openTelemetry;

    private MyClient(OpenTelemetry openTelemetry) {
      this.openTelemetry = openTelemetry;
    }

    // ... library methods omitted
  }

  /** Builder for {@link MyClient}. */
  public static class MyClientBuilder {
    // OpenTelemetry defaults to the GlobalOpenTelemetry instance if set, e.g. by the java agent or
    // by the application, else to a no-op implementation.
    private OpenTelemetry openTelemetry = GlobalOpenTelemetry.getOrNoop();

    /** Explicitly set the OpenTelemetry instance to use. */
    public MyClientBuilder setOpenTelemetry(OpenTelemetry openTelemetry) {
      this.openTelemetry = openTelemetry;
      return this;
    }

    /** Build the client. */
    public MyClient build() {
      return new MyClient(openTelemetry);
    }
  }
}
```


Note that `GlobalOpenTelemetry.getOrNoop()` was designed without the side
effects of `get()` calling `set(..)`, preserving the ability for application
code to later call `set(..)` without triggering an exception.

As a result:

- If the Java agent is present, the instrumentation is initialized with the
  `OpenTelemetry` instance installed by the agent by default.
- If the Java agent is not present, the instrumentation is initialized with a
  no-op implementation by default.
- The user can explicitly override the default by calling `setOpenTelemetry(..)`
  with a separate instance.

**For manual instrumentation, default using:**


<?code-excerpt path-base="examples/java/configuration"?>
<?code-excerpt "src/main/java/otel/GlobalOpenTelemetryManualInstrumentationUsage.java"?>
```java
package otel;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.sdk.autoconfigure.AutoConfiguredOpenTelemetrySdk;

public class GlobalOpenTelemetryManualInstrumentationUsage {

  public static void globalOpenTelemetryUsage() {
    // If GlobalOpenTelemetry is already set, e.g. by the java agent, use it.
    // Else, initialize an OpenTelemetry SDK instance and use it.
    OpenTelemetry openTelemetry =
        GlobalOpenTelemetry.isSet() ? GlobalOpenTelemetry.get() : initializeOpenTelemetry();

    // Install into manual instrumentation. This may involve setting as a singleton in the
    // application's dependency injection framework.
  }

  /** Initialize OpenTelemetry SDK using autoconfiguration. */
  public static OpenTelemetry initializeOpenTelemetry() {
    return AutoConfiguredOpenTelemetrySdk.initialize().getOpenTelemetrySdk();
  }
}
```
<?code-excerpt path-base="examples/java/api"?>


As a result:

- If the Java agent is present, the application initializes manual
  instrumentation with the `OpenTelemetry` instance installed by the agent.
- If the Java agent is not present, the application initializes an
  [OpenTelemetrySdk](../sdk/#opentelemetrysdk) instance and uses it to
  initialize manual instrumentation.

### TracerProvider

[TracerProvider](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/trace/TracerProvider.html)
is the API entry point for traces and provides [Tracers](#tracer). See
[providers and scopes](#providers-and-scopes) for information on providers and
scopes.

#### Tracer

[Tracer](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/trace/Tracer.html)
is used to [record spans](#span) for an instrumentation scope. See
[providers and scopes](#providers-and-scopes) for information on providers and
scopes.

#### Span

[SpanBuilder](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/trace/SpanBuilder.html)
and
[Span](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/trace/Span.html)
are used to construct and record data to spans.

`SpanBuilder` is used to add data to a span before starting it by calling
`Span startSpan()`. Data can be added / updated after starting by calling
various `Span` update methods. The data provided to `SpanBuilder` before
starting is provided as an input to [Samplers](../sdk/#sampler).

The following code snippet explores `SpanBuilder` / `Span` API usage:


<?code-excerpt "src/main/java/otel/SpanUsage.java"?>
```java
package otel;

import static io.opentelemetry.context.Context.current;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import java.util.Arrays;

public class SpanUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void spanUsage(Tracer tracer) {
    // Get a span builder by providing the span name
    Span span =
        tracer
            .spanBuilder("span name")
            // Set span kind
            .setSpanKind(SpanKind.INTERNAL)
            // Set attributes
            .setAttribute(AttributeKey.stringKey("com.acme.string-key"), "value")
            .setAttribute(AttributeKey.booleanKey("com.acme.bool-key"), true)
            .setAttribute(AttributeKey.longKey("com.acme.long-key"), 1L)
            .setAttribute(AttributeKey.doubleKey("com.acme.double-key"), 1.1)
            .setAttribute(
                AttributeKey.stringArrayKey("com.acme.string-array-key"),
                Arrays.asList("value1", "value2"))
            .setAttribute(
                AttributeKey.booleanArrayKey("come.acme.bool-array-key"),
                Arrays.asList(true, false))
            .setAttribute(
                AttributeKey.longArrayKey("come.acme.long-array-key"), Arrays.asList(1L, 2L))
            .setAttribute(
                AttributeKey.doubleArrayKey("come.acme.double-array-key"), Arrays.asList(1.1, 2.2))
            // Optionally omit initializing AttributeKey
            .setAttribute("com.acme.string-key", "value")
            .setAttribute("com.acme.bool-key", true)
            .setAttribute("come.acme.long-key", 1L)
            .setAttribute("come.acme.double-key", 1.1)
            .setAllAttributes(WIDGET_RED_CIRCLE)
            // Uncomment to optionally explicitly set the parent span context. If omitted, the
            // span's parent will be set using Context.current().
            // .setParent(parentContext)
            // Uncomment to optionally add links.
            // .addLink(linkContext, linkAttributes)
            // Start the span
            .startSpan();

    // Check if span is recording before computing additional data
    if (span.isRecording()) {
      // Update the span name with information not available when starting
      span.updateName("new span name");

      // Add additional attributes not available when starting
      span.setAttribute("com.acme.string-key2", "value");

      // Add additional span links not available when starting
      span.addLink(exampleLinkContext());
      // optionally include attributes on the link
      span.addLink(exampleLinkContext(), WIDGET_RED_CIRCLE);

      // Add span events
      span.addEvent("my-event");
      // optionally include attributes on the event
      span.addEvent("my-event", WIDGET_RED_CIRCLE);

      // Record exception, syntactic sugar for a span event with a specific shape
      span.recordException(new RuntimeException("error"));

      // Set the span status
      span.setStatus(StatusCode.OK, "status description");
    }

    // Finally, end the span
    span.end();
  }

  /** Return a dummy link context. */
  private static SpanContext exampleLinkContext() {
    return Span.fromContext(current()).getSpanContext();
  }
}
```


Span parenting is an important aspect of tracing. Each span has an optional
parent. By collecting all the spans in a trace and following each span's parent,
we can construct a hierarchy. The span APIs are built on top of
[context](#context), which allows span context to be implicitly passed around an
application and across threads. When a span is created, its parent is set to the
whatever span is present in `Context.current()` unless there is no span or the
context is explicitly overridden.

Most of the context API usage guidance applies to spans. Span context is
propagated across application boundaries with the
[W3CTraceContextPropagator](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/trace/propagation/W3CTraceContextPropagator.html)
and other [TextMapPropagators](../sdk/#textmappropagator).

The following code snippet explores `Span` API context propagation:


<?code-excerpt "src/main/java/otel/SpanAndContextUsage.java"?>
```java
package otel;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;

public class SpanAndContextUsage {
  private final Tracer tracer;

  SpanAndContextUsage(Tracer tracer) {
    this.tracer = tracer;
  }

  public void nestedSpanUsage() {
    // Start a span. Since we don't call makeCurrent(), we must explicitly call setParent on
    // children. Wrap code in try / finally to ensure we end the span.
    Span span = tracer.spanBuilder("span").startSpan();
    try {
      // Start a child span, explicitly setting the parent.
      Span childSpan =
          tracer
              .spanBuilder("span child")
              // Explicitly set parent.
              .setParent(span.storeInContext(Context.current()))
              .startSpan();
      // Call makeCurrent(), adding childSpan to Context.current(). Spans created inside the scope
      // will have their parent set to childSpan.
      try (Scope childSpanScope = childSpan.makeCurrent()) {
        // Call another method which creates a span. The span's parent will be childSpan since it is
        // started in the childSpan scope.
        doWork();
      } finally {
        childSpan.end();
      }
    } finally {
      span.end();
    }
  }

  private int doWork() {
    Span doWorkSpan = tracer.spanBuilder("doWork").startSpan();
    try (Scope scope = doWorkSpan.makeCurrent()) {
      int result = 0;
      for (int i = 0; i < 10; i++) {
        result += i;
      }
      return result;
    } finally {
      doWorkSpan.end();
    }
  }
}
```


### MeterProvider

[MeterProvider](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/MeterProvider.html)
is the API entry point for metrics and provides [Meters](#meter). See
[providers and scopes](#providers-and-scopes) for information on providers and
scopes.

#### Meter

[Meter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/Meter.html)
is used to obtain instruments for a particular
[instrumentation scope](#providers-and-scopes). See
[providers and scopes](#providers-and-scopes) for information on providers and
scopes. There are a variety of instruments, each with different semantics and
default behavior in the SDK. It's important to choose the right instrument for
each particular use case:

| Instrument                                  | Sync or Async | Description                                                                        | Example                                                 | Default SDK Aggregation                                                                        |
| ------------------------------------------- | ------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| [Counter](#counter)                         | sync          | Record monotonic (positive) values.                                                | Record user logins                                      | [sum (monotonic=true)](/docs/specs/otel/metrics/sdk/#sum-aggregation)                          |
| [Async Counter](#async-counter)             | async         | Observe monotonic sums.                                                            | Observe number of classes loaded in the JVM             | [sum (monotonic=true)](/docs/specs/otel/metrics/sdk/#sum-aggregation)                          |
| [UpDownCounter](#updowncounter)             | sync          | Record non-monotonic (positive and negative) values.                               | Record when items are added to and removed from a queue | [sum (monotonic=false)](/docs/specs/otel/metrics/sdk/#sum-aggregation)                         |
| [Async UpDownCounter](#async-updowncounter) | async         | Observe non-monotonic (positive and negative) sums.                                | Observe JVM memory pool usage                           | [sum (monotonic=false)](/docs/specs/otel/metrics/sdk/#sum-aggregation)                         |
| [Histogram](#histogram)                     | sync          | Record monotonic (positive) values where the distribution is important.            | Record duration of HTTP requests processed by server    | [ExplicitBucketHistogram](/docs/specs/otel/metrics/sdk/#explicit-bucket-histogram-aggregation) |
| [Gauge](#gauge)                             | sync          | Record the latest value where spatial re-aggregation does not make sense **[1]**.  | Record temperature                                      | [LastValue](/docs/specs/otel/metrics/sdk/#last-value-aggregation)                              |
| [Async Gauge](#async-gauge)                 | async         | Observe the latest value where spatial re-aggregation does not make sense **[1]**. | Observe CPU utilization                                 | [LastValue](/docs/specs/otel/metrics/sdk/#last-value-aggregation)                              |

**[1]**: Spatial re-aggregation is the process of merging attribute streams by
dropping attributes which are not needed. For example, given series with
attributes `{"color": "red", "shape": "square"}`,
`{"color": "blue", "shape": "square"}`, you can perform spatial re-aggregation
by dropping the `color` attribute, and merging the series where the attributes
are equal after dropping `color`. Most aggregations have a useful spatial
aggregation merge function (i.e. sums are summed together), but gauges
aggregated by the `LastValue` aggregation are the exception. For example,
suppose the series mentioned previously are tracking the temperature of widgets.
How do you merge the series when you drop the `color` attribute? There is no
good answer besides flipping a coin and selecting a random value.

The instrument APIs have share a variety of features:

- Created using the builder pattern.
- Required instrument name.
- Optional unit and description.
- Record values which are `long` or `double`, which is configured via the
  builder.

See [metric guidelines](/docs/specs/semconv/general/metrics/#general-guidelines)
for details on metric naming and units.

See
[guidelines for instrumentation library authors](/docs/specs/otel/metrics/supplementary-guidelines/#guidelines-for-instrumentation-library-authors)
for additional guidance on instrument selection.

#### Counter

[LongCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/LongCounter.html)
and
[DoubleCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/DoubleCounter.html)
are used to record monotonic (positive) values.

The following code snippet explores counter API usage:


<?code-excerpt "src/main/java/otel/CounterUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;
import static otel.Util.customContext;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.Meter;

public class CounterUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void counterUsage(Meter meter) {
    // Construct a counter to record measurements that are always positive (monotonically
    // increasing).
    LongCounter counter =
        meter
            .counterBuilder("fully.qualified.counter")
            .setDescription("A count of produced widgets")
            .setUnit("{widget}")
            // optionally change the type to double
            // .ofDoubles()
            .build();

    // Record a measurement with no attributes or context.
    // Attributes defaults to Attributes.empty(), context to Context.current().
    counter.add(1L);

    // Record a measurement with attributes, using pre-allocated attributes whenever possible.
    counter.add(1L, WIDGET_RED_CIRCLE);
    // Sometimes, attributes must be computed using application context.
    counter.add(
        1L, Attributes.of(WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));

    // Record a measurement with attributes, and context.
    // Most users will opt to omit the context argument, preferring the default Context.current().
    counter.add(1L, WIDGET_RED_CIRCLE, customContext());
  }
}
```


#### Async Counter

[ObservableLongCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/ObservableLongCounter.html)
and
[ObservableDoubleCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/ObservableDoubleCounter.html)
are used to observe monotonic (positive) sums.

The following code snippet explores async counter API usage:


<?code-excerpt "src/main/java/otel/AsyncCounterUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.metrics.ObservableLongCounter;
import java.util.concurrent.atomic.AtomicLong;

public class AsyncCounterUsage {
  // Pre-allocate attributes whenever possible
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void asyncCounterUsage(Meter meter) {
    AtomicLong widgetCount = new AtomicLong();

    // Construct an async counter to observe an existing counter in a callback
    ObservableLongCounter asyncCounter =
        meter
            .counterBuilder("fully.qualified.counter")
            .setDescription("A count of produced widgets")
            .setUnit("{widget}")
            // Uncomment to optionally change the type to double
            // .ofDoubles()
            .buildWithCallback(
                // the callback is invoked when a MetricReader reads metrics
                observableMeasurement -> {
                  long currentWidgetCount = widgetCount.get();

                  // Record a measurement with no attributes.
                  // Attributes defaults to Attributes.empty().
                  observableMeasurement.record(currentWidgetCount);

                  // Record a measurement with attributes, using pre-allocated attributes whenever
                  // possible.
                  observableMeasurement.record(currentWidgetCount, WIDGET_RED_CIRCLE);
                  // Sometimes, attributes must be computed using application context.
                  observableMeasurement.record(
                      currentWidgetCount,
                      Attributes.of(
                          WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));
                });

    // Optionally close the counter to unregister the callback when required
    asyncCounter.close();
  }
}
```


#### UpDownCounter

[LongUpDownCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/LongUpDownCounter.html)
and
[DoubleUpDownCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/DoubleUpDownCounter.html)
are used to record non-monotonic (positive and negative) values.

The following code snippet explores updowncounter API usage:


<?code-excerpt "src/main/java/otel/UpDownCounterUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;
import static otel.Util.customContext;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.LongUpDownCounter;
import io.opentelemetry.api.metrics.Meter;

public class UpDownCounterUsage {

  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void usage(Meter meter) {
    // Construct an updowncounter to record measurements that go up and down.
    LongUpDownCounter upDownCounter =
        meter
            .upDownCounterBuilder("fully.qualified.updowncounter")
            .setDescription("Current length of widget processing queue")
            .setUnit("{widget}")
            // Uncomment to optionally change the type to double
            // .ofDoubles()
            .build();

    // Record a measurement with no attributes or context.
    // Attributes defaults to Attributes.empty(), context to Context.current().
    upDownCounter.add(1L);

    // Record a measurement with attributes, using pre-allocated attributes whenever possible.
    upDownCounter.add(-1L, WIDGET_RED_CIRCLE);
    // Sometimes, attributes must be computed using application context.
    upDownCounter.add(
        -1L, Attributes.of(WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));

    // Record a measurement with attributes, and context.
    // Most users will opt to omit the context argument, preferring the default Context.current().
    upDownCounter.add(1L, WIDGET_RED_CIRCLE, customContext());
  }
}
```


#### Async UpDownCounter

[ObservableLongUpDownCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/ObservableLongUpDownCounter.html)
and
[ObservableDoubleUpDownCounter](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/ObservableDoubleUpDownCounter.html)
are used to observe non-monotonic (positive and negative) sums.

The following code snippet explores async updowncounter API usage:


<?code-excerpt "src/main/java/otel/AsyncUpDownCounterUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.metrics.ObservableLongUpDownCounter;
import java.util.concurrent.atomic.AtomicLong;

public class AsyncUpDownCounterUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void asyncUpDownCounterUsage(Meter meter) {
    AtomicLong queueLength = new AtomicLong();

    // Construct an async updowncounter to observe an existing up down counter in a callback
    ObservableLongUpDownCounter asyncUpDownCounter =
        meter
            .upDownCounterBuilder("fully.qualified.updowncounter")
            .setDescription("Current length of widget processing queue")
            .setUnit("{widget}")
            // Uncomment to optionally change the type to double
            // .ofDoubles()
            .buildWithCallback(
                // the callback is invoked when a MetricReader reads metrics
                observableMeasurement -> {
                  long currentWidgetCount = queueLength.get();

                  // Record a measurement with no attributes.
                  // Attributes defaults to Attributes.empty().
                  observableMeasurement.record(currentWidgetCount);

                  // Record a measurement with attributes, using pre-allocated attributes whenever
                  // possible.
                  observableMeasurement.record(currentWidgetCount, WIDGET_RED_CIRCLE);
                  // Sometimes, attributes must be computed using application context.
                  observableMeasurement.record(
                      currentWidgetCount,
                      Attributes.of(
                          WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));
                });

    // Optionally close the counter to unregister the callback when required
    asyncUpDownCounter.close();
  }
}
```


#### Histogram

[DoubleHistogram](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/DoubleHistogram.html)
and
[LongHistogram](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/LongHistogram.html)
are used to record monotonic (positive) values where the distribution is
important.

The following code snippet explores histogram API usage:


<?code-excerpt "src/main/java/otel/HistogramUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;
import static otel.Util.customContext;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.metrics.Meter;

public class HistogramUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void histogramUsage(Meter meter) {
    // Construct a histogram to record measurements where the distribution is important.
    DoubleHistogram histogram =
        meter
            .histogramBuilder("fully.qualified.histogram")
            .setDescription("Length of time to process a widget")
            .setUnit("s")
            // Uncomment to optionally provide advice on useful default explicit bucket boundaries
            // .setExplicitBucketBoundariesAdvice(Arrays.asList(1.0, 2.0, 3.0))
            // Uncomment to optionally change the type to long
            // .ofLongs()
            .build();

    // Record a measurement with no attributes or context.
    // Attributes defaults to Attributes.empty(), context to Context.current().
    histogram.record(1.1);

    // Record a measurement with attributes, using pre-allocated attributes whenever possible.
    histogram.record(2.2, WIDGET_RED_CIRCLE);
    // Sometimes, attributes must be computed using application context.
    histogram.record(
        3.2, Attributes.of(WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));

    // Record a measurement with attributes, and context.
    // Most users will opt to omit the context argument, preferring the default Context.current().
    histogram.record(4.4, WIDGET_RED_CIRCLE, customContext());
  }
}
```


#### Gauge

[DoubleGauge](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/DoubleGauge.html)
and
[LongGauge](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/LongGauge.html)
are used to record the latest value where spatial re-aggregation does not make
sense.

The following code snippet explores gauge API usage:


<?code-excerpt "src/main/java/otel/GaugeUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;
import static otel.Util.customContext;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleGauge;
import io.opentelemetry.api.metrics.Meter;

public class GaugeUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void gaugeUsage(Meter meter) {
    // Construct a gauge to record measurements as they occur, which cannot be spatially
    // re-aggregated.
    DoubleGauge gauge =
        meter
            .gaugeBuilder("fully.qualified.gauge")
            .setDescription("The current temperature of the widget processing line")
            .setUnit("K")
            // Uncomment to optionally change the type to long
            // .ofLongs()
            .build();

    // Record a measurement with no attributes or context.
    // Attributes defaults to Attributes.empty(), context to Context.current().
    gauge.set(273.0);

    // Record a measurement with attributes, using pre-allocated attributes whenever possible.
    gauge.set(273.0, WIDGET_RED_CIRCLE);
    // Sometimes, attributes must be computed using application context.
    gauge.set(
        273.0,
        Attributes.of(WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));

    // Record a measurement with attributes, and context.
    // Most users will opt to omit the context argument, preferring the default Context.current().
    gauge.set(1L, WIDGET_RED_CIRCLE, customContext());
  }
}
```


#### Async Gauge

[ObservableDoubleGauge](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/ObservableDoubleGauge.html)
and
[ObservableLongGauge](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/metrics/ObservableLongGauge.html)
are used to observe the latest value where spatial re-aggregation does not make
sense.

The following code snippet explores async gauge API usage:


<?code-excerpt "src/main/java/otel/AsyncGaugeUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_SHAPE;
import static otel.Util.computeWidgetColor;
import static otel.Util.computeWidgetShape;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.metrics.ObservableDoubleGauge;
import java.util.concurrent.atomic.AtomicReference;

public class AsyncGaugeUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void asyncGaugeUsage(Meter meter) {
    AtomicReference<Double> processingLineTemp = new AtomicReference<>(273.0);

    // Construct an async gauge to observe an existing gauge in a callback
    ObservableDoubleGauge asyncGauge =
        meter
            .gaugeBuilder("fully.qualified.gauge")
            .setDescription("The current temperature of the widget processing line")
            .setUnit("K")
            // Uncomment to optionally change the type to long
            // .ofLongs()
            .buildWithCallback(
                // the callback is invoked when a MetricReader reads metrics
                observableMeasurement -> {
                  double currentWidgetCount = processingLineTemp.get();

                  // Record a measurement with no attributes.
                  // Attributes defaults to Attributes.empty().
                  observableMeasurement.record(currentWidgetCount);

                  // Record a measurement with attributes, using pre-allocated attributes whenever
                  // possible.
                  observableMeasurement.record(currentWidgetCount, WIDGET_RED_CIRCLE);
                  // Sometimes, attributes must be computed using application context.
                  observableMeasurement.record(
                      currentWidgetCount,
                      Attributes.of(
                          WIDGET_SHAPE, computeWidgetShape(), WIDGET_COLOR, computeWidgetColor()));
                });

    // Optionally close the gauge to unregister the callback when required
    asyncGauge.close();
  }
}
```


### LoggerProvider

[LoggerProvider](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/logs/LoggerProvider.html)
is the API entry point for logs and provides [Loggers](#logger). See
[providers and scopes](#providers-and-scopes) for information on providers and
scopes.

> [!WARNING]
>
> {{% param logBridgeWarning %}}

#### Logger

[Logger](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/logs/Logger.html)
is used to [emit log records](#logrecordbuilder) for an
[instrumentation scope](#providers-and-scopes). See
[providers and scopes](#providers-and-scopes) for information on providers and
scopes.

#### LogRecordBuilder

[LogRecordBuilder](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/logs/LogRecordBuilder.html)
is used to construct and emit log records.

The following code snippet explores `LogRecordBuilder` API usage:


<?code-excerpt "src/main/java/otel/LogRecordUsage.java"?>
```java
package otel;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.common.Value;
import io.opentelemetry.api.logs.Logger;
import io.opentelemetry.api.logs.Severity;
import java.util.Arrays;
import java.util.Map;
import java.util.concurrent.TimeUnit;

public class LogRecordUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void logRecordUsage(Logger logger) {
    logger
        .logRecordBuilder()
        // Set body. Note, setBody(..) is called multiple times for demonstration purposes but only
        // the last call is used.
        // Set the body to a string, syntactic sugar for setBody(Value.of("log message"))
        .setBody("log message")
        // Optionally set the body to a Value to record arbitrarily complex structured data
        .setBody(Value.of("log message"))
        .setBody(Value.of(1L))
        .setBody(Value.of(1.1))
        .setBody(Value.of(true))
        .setBody(Value.of(new byte[] {'a', 'b', 'c'}))
        .setBody(Value.of(Value.of("entry1"), Value.of("entry2")))
        .setBody(
            Value.of(
                Map.of(
                    "stringKey",
                    Value.of("entry1"),
                    "mapKey",
                    Value.of(Map.of("stringKey", Value.of("entry2"))))))
        // Set severity
        .setSeverity(Severity.DEBUG)
        .setSeverityText("debug")
        // Set timestamp
        .setTimestamp(System.currentTimeMillis(), TimeUnit.MILLISECONDS)
        // Optionally set the timestamp when the log was observed
        .setObservedTimestamp(System.currentTimeMillis(), TimeUnit.MILLISECONDS)
        // Set attributes
        .setAttribute(AttributeKey.stringKey("com.acme.string-key"), "value")
        .setAttribute(AttributeKey.booleanKey("com.acme.bool-key"), true)
        .setAttribute(AttributeKey.longKey("com.acme.long-key"), 1L)
        .setAttribute(AttributeKey.doubleKey("com.acme.double-key"), 1.1)
        .setAttribute(
            AttributeKey.stringArrayKey("com.acme.string-array-key"),
            Arrays.asList("value1", "value2"))
        .setAttribute(
            AttributeKey.booleanArrayKey("come.acme.bool-array-key"), Arrays.asList(true, false))
        .setAttribute(AttributeKey.longArrayKey("come.acme.long-array-key"), Arrays.asList(1L, 2L))
        .setAttribute(
            AttributeKey.doubleArrayKey("come.acme.double-array-key"), Arrays.asList(1.1, 2.2))
        .setAllAttributes(WIDGET_RED_CIRCLE)
        // Uncomment to optionally explicitly set the context used to correlate with spans. If
        // omitted, Context.current() is used.
        // .setContext(context)
        // Emit the log record
        .emit();
  }
}
```


### No-op implementation

The `OpenTelemetry#noop()` method provides access to a no-op implementation of
[OpenTelemetry](#opentelemetry) and all API components it provides access to. As
the name suggests, the no-op implementation does nothing and is designed to have
no impact on performance. Instrumentation may see impact on performance even
when the no-op is used if it is computing / allocating attribute values and
other data required to record the telemetry. The no-op is a useful default
instance of `OpenTelemetry` when a user has not configured and installed a
concrete implementation such as the [SDK](../sdk/).

The following code snippet explores `OpenTelemetry#noop()` API usage:


<?code-excerpt "src/main/java/otel/NoopUsage.java"?>
```java
package otel;

import static otel.Util.WIDGET_COLOR;
import static otel.Util.WIDGET_RED_CIRCLE;
import static otel.Util.WIDGET_SHAPE;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.logs.Logger;
import io.opentelemetry.api.logs.Severity;
import io.opentelemetry.api.metrics.DoubleGauge;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.LongUpDownCounter;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;

public class NoopUsage {
  private static final String SCOPE_NAME = "fully.qualified.name";

  public static void noopUsage() {
    // Access the no-op OpenTelemetry instance
    OpenTelemetry noopOpenTelemetry = OpenTelemetry.noop();

    // No-op tracing
    Tracer noopTracer = OpenTelemetry.noop().getTracer(SCOPE_NAME);
    noopTracer
        .spanBuilder("span name")
        .startSpan()
        .setAttribute(WIDGET_SHAPE, "square")
        .setStatus(StatusCode.OK)
        .addEvent("event-name", Attributes.builder().put(WIDGET_COLOR, "red").build())
        .end();

    // No-op metrics
    Attributes attributes = WIDGET_RED_CIRCLE;
    Meter noopMeter = OpenTelemetry.noop().getMeter(SCOPE_NAME);
    DoubleHistogram histogram = noopMeter.histogramBuilder("fully.qualified.histogram").build();
    histogram.record(1.0, attributes);
    // counter
    LongCounter counter = noopMeter.counterBuilder("fully.qualified.counter").build();
    counter.add(1, attributes);
    // async counter
    noopMeter
        .counterBuilder("fully.qualified.counter")
        .buildWithCallback(observable -> observable.record(10, attributes));
    // updowncounter
    LongUpDownCounter upDownCounter =
        noopMeter.upDownCounterBuilder("fully.qualified.updowncounter").build();
    // async updowncounter
    noopMeter
        .upDownCounterBuilder("fully.qualified.updowncounter")
        .buildWithCallback(observable -> observable.record(10, attributes));
    upDownCounter.add(-1, attributes);
    // gauge
    DoubleGauge gauge = noopMeter.gaugeBuilder("fully.qualified.gauge").build();
    gauge.set(1.1, attributes);
    // async gauge
    noopMeter
        .gaugeBuilder("fully.qualified.gauge")
        .buildWithCallback(observable -> observable.record(10, attributes));

    // No-op logs
    Logger noopLogger = OpenTelemetry.noop().getLogsBridge().get(SCOPE_NAME);
    noopLogger
        .logRecordBuilder()
        .setBody("log message")
        .setAttribute(WIDGET_SHAPE, "square")
        .setSeverity(Severity.INFO)
        .emit();
  }
}
```


### Semantic attributes

The [semantic conventions](/docs/specs/semconv/) describe how to collect
telemetry in a standardized way for common operations. This includes an
[attribute registry](/docs/specs/semconv/registry/attributes/), which enumerates
definitions for all attributes referenced in the conventions, organized by
domain. The
[semantic-conventions-java](https://github.com/open-telemetry/semantic-conventions-java)
project generates constants from the semantic conventions, which can be used to
help instrumentation conform:

| Description                                        | Artifact                                                                                     |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Generated code for stable semantic conventions     | `io.opentelemetry.semconv:opentelemetry-semconv:{{% param vers.semconv %}}-alpha`            |
| Generated code for incubating semantic conventions | `io.opentelemetry.semconv:opentelemetry-semconv-incubating:{{% param vers.semconv %}}-alpha` |

> [!NOTE]
>
> While both `opentelemetry-semconv` and `opentelemetry-semconv-incubating`
> include the `-alpha` suffix and are subject to breaking changes, the intent is
> to stabilize `opentelemetry-semconv` and leave the `-alpha` suffix on
> `opentelemetry-semconv-incubating` permanently. Libraries can use
> `opentelemetry-semconv-incubating` for testing, but should not include it as a
> dependency: since attributes may come and go from version to version,
> including it as a dependency may expose end users to runtime errors when
> transitive version conflicts occur.

The attribute constants generated from semantic conventions are instances of
`AttributeKey<T>`, and can be used anywhere the OpenTelemetry API accepts
attributes.

The following code snippet explores semantic convention attribute API usage:


<?code-excerpt "src/main/java/otel/SemanticAttributesUsage.java"?>
```java
package otel;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.semconv.HttpAttributes;
import io.opentelemetry.semconv.ServerAttributes;
import io.opentelemetry.semconv.incubating.HttpIncubatingAttributes;

public class SemanticAttributesUsage {
  public static void semanticAttributesUsage() {
    // Semantic attributes are organized by top-level domain and whether they are stable or
    // incubating.
    // For example:
    // - stable attributes starting with http.* are in the HttpAttributes class.
    // - stable attributes starting with server.* are in the ServerAttributes class.
    // - incubating attributes starting with http.* are in the HttpIncubatingAttributes class.
    // Attribute keys which define an enumeration of values are accessible in an inner
    // {AttributeKey}Values class.
    // For example, the enumeration of http.request.method values is available in the
    // HttpAttributes.HttpRequestMethodValues class.
    Attributes attributes =
        Attributes.builder()
            .put(HttpAttributes.HTTP_REQUEST_METHOD, HttpAttributes.HttpRequestMethodValues.GET)
            .put(HttpAttributes.HTTP_ROUTE, "/users/:id")
            .put(ServerAttributes.SERVER_ADDRESS, "example")
            .put(ServerAttributes.SERVER_PORT, 8080L)
            .put(HttpIncubatingAttributes.HTTP_RESPONSE_BODY_SIZE, 1024)
            .build();
  }
}
```


### Baggage

[Baggage](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/baggage/Baggage.html)
is a bundle of application defined key-value pairs associated with a distributed
request or workflow execution. Baggage keys and values are strings, and values
have optional string metadata. Telemetry can be enriched with data from baggage
by configuring the [SDK](../sdk/) to add entries as attributes to spans,
metrics, and log records. The baggage API is built on top of
[context](#context), which allows span context to be implicitly passed around an
application and across threads. Most of the context API usage guidance applies
to baggage.

Baggage is propagated across application boundaries with the
[W3CBaggagePropagator](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-api/latest/io/opentelemetry/api/baggage/propagation/W3CBaggagePropagator.html)
(see [TextMapPropagator](../sdk/#textmappropagator) for details).

The following code snippet explores `Baggage` API usage:


<?code-excerpt "src/main/java/otel/BaggageUsage.java"?>
```java
package otel;

import static io.opentelemetry.context.Context.current;

import io.opentelemetry.api.baggage.Baggage;
import io.opentelemetry.api.baggage.BaggageEntry;
import io.opentelemetry.api.baggage.BaggageEntryMetadata;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.context.Scope;
import java.util.Map;
import java.util.stream.Collectors;

public class BaggageUsage {
  private static final Attributes WIDGET_RED_CIRCLE = Util.WIDGET_RED_CIRCLE;

  public static void baggageUsage() {
    // Access current baggage with Baggage.current()
    // output => context baggage: {}
    Baggage currentBaggage = Baggage.current();
    System.out.println("current baggage: " + asString(currentBaggage));
    // ...or from a Context
    currentBaggage = Baggage.fromContext(current());

    // Baggage has a variety of methods for manipulating and reading data.
    // Convert to builder and add entries:
    Baggage newBaggage =
        Baggage.current().toBuilder()
            .put("shopId", "abc123")
            .put("shopName", "opentelemetry-demo", BaggageEntryMetadata.create("metadata"))
            .build();
    // ...or uncomment to start from empty
    // newBaggage = Baggage.empty().toBuilder().put("shopId", "abc123").build();
    // output => new baggage: {shopId=abc123(), shopName=opentelemetry-demo(metadata)}
    System.out.println("new baggage: " + asString(newBaggage));
    // Read an entry:
    String shopIdValue = newBaggage.getEntryValue("shopId");
    // Inspect size:
    int size = newBaggage.size();
    boolean isEmpty = newBaggage.isEmpty();
    // Convert to map representation:
    Map<String, BaggageEntry> map = newBaggage.asMap();
    // Iterate through entries:
    newBaggage.forEach((s, baggageEntry) -> {});

    // The current baggage still doesn't contain the new entries
    // output => context baggage: {}
    System.out.println("current baggage: " + asString(Baggage.current()));

    // Calling Baggage.makeCurrent() sets Baggage.current() to the baggage until the scope is
    // closed, upon which Baggage.current() is restored to the state prior to when
    // Baggage.makeCurrent() was called.
    try (Scope scope = newBaggage.makeCurrent()) {
      // The current baggage now contains the added value
      // output => context baggage: {shopId=abc123(), shopName=opentelemetry-demo(metadata)}
      System.out.println("current baggage: " + asString(Baggage.current()));
    }

    // The current baggage no longer contains the new entries:
    // output => context baggage: {}
    System.out.println("current baggage: " + asString(Baggage.current()));
  }

  private static String asString(Baggage baggage) {
    return baggage.asMap().entrySet().stream()
        .map(
            entry ->
                String.format(
                    "%s=%s(%s)",
                    entry.getKey(),
                    entry.getValue().getValue(),
                    entry.getValue().getMetadata().getValue()))
        .collect(Collectors.joining(", ", "{", "}"));
  }
}
```