## Context API

The `io.opentelemetry:opentelemetry-api-context:{{% param vers.otel %}}`
artifact contains standalone APIs (i.e. packaged separately from
[OpenTelemetry API](#opentelemetry-api)) for propagating context throughout an
application and across application boundaries.

It consists of:

- [Context](#context): An immutable bundle of key value pairs which is
  implicitly or explicitly propagated through an application.
- [ContextStorage](#contextstorage): A mechanism for storing and retrieving the
  current context, defaulting to thread local.
- [ContextPropagators](#context): A container of registered propagators for
  propagating `Context` across application boundaries.

The `io.opentelemetry:opentelemetry-extension-kotlint:{{% param vers.otel %}}`
is an extension with tools for propagating context into coroutines.

### Context

[Context](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-context/latest/io/opentelemetry/context/Context.html)
is an immutable bundle of key value pairs, with utilities for implicitly
propagating through an application and across threads. Implicit propagation
means that the context can be accessed without explicitly passing it as an
argument. Context is a recurring concept in the OpenTelemetry API:

- The current active [Span](#span) is stored in context, and by default a span's
  parent is assigned to whatever span is currently in context.
- The measurements recorded to [metric instruments](#meter) accept a context
  argument, used to link measurements to spans via
  [exemplars](/docs/specs/otel/metrics/data-model/#exemplars) and defaulting to
  whatever span is currently in context.
- [LogRecords](#logrecordbuilder) accept a context argument, used to link log
  record spans and defaulting to whatever span is currently in context.

The following code snippet explores `Context` API usage:


<?code-excerpt "src/main/java/otel/ContextUsage.java"?>
```java
package otel;

import io.opentelemetry.context.Context;
import io.opentelemetry.context.ContextKey;
import io.opentelemetry.context.Scope;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class ContextUsage {
  public static void contextUsage() throws Exception {
    // Define an example context key
    ContextKey<String> exampleContextKey = ContextKey.named("example-context-key");

    // Context doesn't contain the key until we add it
    // Context.current() accesses the current context
    // output => current context value: null
    System.out.println("current context value: " + Context.current().get(exampleContextKey));

    // Add entry to context
    Context context = Context.current().with(exampleContextKey, "value");

    // The local context var contains the added value
    // output => context value: value
    System.out.println("context value: " + context.get(exampleContextKey));
    // The current context still doesn't contain the value
    // output => current context value: null
    System.out.println("current context value: " + Context.current().get(exampleContextKey));

    // Calling context.makeCurrent() sets Context.current() to the context until the scope is
    // closed, upon which Context.current() is restored to the state prior to when
    // context.makeCurrent() was called. The resulting Scope implements AutoCloseable and is
    // normally used in a try-with-resources block. Failure to call Scope.close() is an error and
    // may cause memory leaks or other issues.
    try (Scope scope = context.makeCurrent()) {
      // The current context now contains the added value
      // output => context value: value
      System.out.println("context value: " + Context.current().get(exampleContextKey));
    }

    // The local context var still contains the added value
    // output => context value: value
    System.out.println("context value: " + context.get(exampleContextKey));
    // The current context no longer contains the value
    // output => current context value: null
    System.out.println("current context value: " + Context.current().get(exampleContextKey));

    ExecutorService executorService = Executors.newSingleThreadExecutor();
    ScheduledExecutorService scheduledExecutorService = Executors.newScheduledThreadPool(1);

    // Context instances can be explicitly passed around application code, but it's more convenient
    // to use implicit context, calling Context.makeCurrent() and accessing via Context.current().
    // Context provides a number of utilities for implicit context propagation. These utilities wrap
    // utility classes like Scheduler, ExecutorService, ScheduledExecutorService, Runnable,
    // Callable, Consumer, Supplier, Function, etc and modify their behavior to call
    // Context.makeCurrent() before running.
    context.wrap(ContextUsage::callable).call();
    context.wrap(ContextUsage::runnable).run();
    context.wrap(executorService).submit(ContextUsage::runnable);
    context.wrap(scheduledExecutorService).schedule(ContextUsage::runnable, 1, TimeUnit.SECONDS);
    context.wrapConsumer(ContextUsage::consumer).accept(new Object());
    context.wrapConsumer(ContextUsage::biConsumer).accept(new Object(), new Object());
    context.wrapFunction(ContextUsage::function).apply(new Object());
    context.wrapSupplier(ContextUsage::supplier).get();
  }

  /** Example {@link java.util.concurrent.Callable}. */
  private static Object callable() {
    return new Object();
  }

  /** Example {@link Runnable}. */
  private static void runnable() {}

  /** Example {@link java.util.function.Consumer}. */
  private static void consumer(Object object) {}

  /** Example {@link java.util.function.BiConsumer}. */
  private static void biConsumer(Object object1, Object object2) {}

  /** Example {@link java.util.function.Function}. */
  private static Object function(Object object) {
    return object;
  }

  /** Example {@link java.util.function.Supplier}. */
  private static Object supplier() {
    return new Object();
  }
}
```


### ContextStorage

[ContextStorage](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-context/latest/io/opentelemetry/context/ContextStorage.html)
is a mechanism for storing and retrieving the current `Context`.

The default `ContextStorage` implementation stores `Context` in thread local.

### ContextPropagators

[ContextPropagators](https://www.javadoc.io/doc/io.opentelemetry/opentelemetry-context/latest/io/opentelemetry/context/propagation/ContextPropagators.html)
is a container of registered propagators for propagating `Context` across
application boundaries. Context is injected into a carrier when leaving an
application (i.e. an outbound HTTP request), and extracted from a carrier when
entering an application (i.e. serving an HTTP request).

See [SDK TextMapPropagators](../sdk/#textmappropagator) for propagator
implementations.

The following code snippet explores `ContextPropagators` API for injection:


<?code-excerpt "src/main/java/otel/InjectContextUsage.java"?>
```java
package otel;

import io.opentelemetry.api.baggage.propagation.W3CBaggagePropagator;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.context.propagation.TextMapPropagator;
import io.opentelemetry.context.propagation.TextMapSetter;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class InjectContextUsage {
  private static final TextMapSetter<HttpRequest.Builder> TEXT_MAP_SETTER = new HttpRequestSetter();

  public static void injectContextUsage() throws Exception {
    // Create a ContextPropagators instance which propagates w3c trace context and w3c baggage
    ContextPropagators propagators =
        ContextPropagators.create(
            TextMapPropagator.composite(
                W3CTraceContextPropagator.getInstance(), W3CBaggagePropagator.getInstance()));

    // Create an HttpRequest builder
    HttpClient httpClient = HttpClient.newBuilder().build();
    HttpRequest.Builder requestBuilder =
        HttpRequest.newBuilder().uri(new URI("http://127.0.0.1:8080/resource")).GET();

    // Given a ContextPropagators instance, inject the current context into the HTTP request carrier
    propagators.getTextMapPropagator().inject(Context.current(), requestBuilder, TEXT_MAP_SETTER);

    // Send the request with the injected context
    httpClient.send(requestBuilder.build(), HttpResponse.BodyHandlers.discarding());
  }

  /** {@link TextMapSetter} with a {@link HttpRequest.Builder} carrier. */
  private static class HttpRequestSetter implements TextMapSetter<HttpRequest.Builder> {
    @Override
    public void set(HttpRequest.Builder carrier, String key, String value) {
      if (carrier == null) {
        return;
      }
      carrier.setHeader(key, value);
    }
  }
}
```


The following code snippet explores `ContextPropagators` API for extraction:


<?code-excerpt "src/main/java/otel/ExtractContextUsage.java"?>
```java
package otel;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import io.opentelemetry.api.baggage.propagation.W3CBaggagePropagator;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.context.propagation.TextMapGetter;
import io.opentelemetry.context.propagation.TextMapPropagator;
import io.opentelemetry.context.propagation.TextMapSetter;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;

public class ExtractContextUsage {
  private static final TextMapGetter<HttpExchange> TEXT_MAP_GETTER = new HttpRequestGetter();

  public static void extractContextUsage() throws Exception {
    // Create a ContextPropagators instance which propagates w3c trace context and w3c baggage
    ContextPropagators propagators =
        ContextPropagators.create(
            TextMapPropagator.composite(
                W3CTraceContextPropagator.getInstance(), W3CBaggagePropagator.getInstance()));

    // Create a server, which uses the propagators to extract context from requests
    HttpServer server = HttpServer.create(new InetSocketAddress(8080), 0);
    server.createContext("/path", new Handler(propagators));
    server.setExecutor(null);
    server.start();
  }

  private static class Handler implements HttpHandler {
    private final ContextPropagators contextPropagators;

    private Handler(ContextPropagators contextPropagators) {
      this.contextPropagators = contextPropagators;
    }

    @Override
    public void handle(HttpExchange exchange) throws IOException {
      // Extract the context from the request and make the context current
      Context extractedContext =
          contextPropagators
              .getTextMapPropagator()
              .extract(Context.current(), exchange, TEXT_MAP_GETTER);
      try (Scope scope = extractedContext.makeCurrent()) {
        // Do work with the extracted context
      } finally {
        String response = "success";
        exchange.sendResponseHeaders(200, response.length());
        OutputStream os = exchange.getResponseBody();
        os.write(response.getBytes(StandardCharsets.UTF_8));
        os.close();
      }
    }
  }

  /** {@link TextMapSetter} with a {@link HttpExchange} carrier. */
  private static class HttpRequestGetter implements TextMapGetter<HttpExchange> {
    @Override
    public Iterable<String> keys(HttpExchange carrier) {
      return carrier.getRequestHeaders().keySet();
    }

    @Override
    public String get(HttpExchange carrier, String key) {
      if (carrier == null) {
        return null;
      }
      List<String> headers = carrier.getRequestHeaders().get(key);
      if (headers == null || headers.isEmpty()) {
        return null;
      }
      return headers.get(0);
    }
  }
}
```