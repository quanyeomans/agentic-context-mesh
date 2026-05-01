## Registering the custom filter on the BatchSpanProcessor
io.opentelemetry.sdk.trace.export.BatchSpanProcessor = io.opentelemetry.extension.logging.IgnoreExportErrorsFilter
```

### OTLP exporters

The [span exporter](#spanexporter), [metric exporter](#metricexporter), and
[log exporter](#logrecordexporter) sections describe OTLP exporters of the form:

- `OtlpHttp{Signal}Exporter`, which exports data via OTLP `http/protobuf`
- `OtlpGrpc{Signal}Exporter`, which exports data via OTLP `grpc`

The exporters for all signals are available via
`io.opentelemetry:opentelemetry-exporter-otlp:{{% param vers.otel %}}`, and have
significant overlap across `grpc` and `http/protobuf` versions of the OTLP
protocol, and between signals. The following sections elaborate on these key
concepts:

- [Senders](#senders): an abstraction for a different HTTP / gRPC client
  libraries.
- [Authentication](#authentication) options for OTLP exporters.

#### Senders

The OTLP exporters depend on various client libraries to execute HTTP and gRPC
requests. There is no single HTTP / gRPC client library which satisfies all use
cases in the Java ecosystem:

- Java 11+ brings the built-in `java.net.http.HttpClient`, but
  `opentelemetry-java` needs to support Java 8+ users, and this can't be used to
  export via `gRPC` because there is no support for trailer headers.
- [OkHttp](https://square.github.io/okhttp/) provides a powerful HTTP client
  with support for trailer headers, but depends on the kotlin standard library.
- [grpc-java](https://github.com/grpc/grpc-java) provides its own
  `ManagedChannel` abstraction with various
  [transport implementations](https://github.com/grpc/grpc-java#transport), but
  is not suitable for `http/protobuf`.

In order to accommodate various use cases, `opentelemetry-exporter-otlp` uses an
internal "sender" abstraction, with a variety of implementations to reflect
application constraints. To choose another implementation, exclude the
`io.opentelemetry:opentelemetry-exporter-sender-okhttp` default dependency, and
add a dependency on the alternative.

| Artifact                                                                                              | Description                                               | OTLP Protocols          | Default |
| ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------- | ----------------------- | ------- |
| `io.opentelemetry:opentelemetry-exporter-sender-okhttp:{{% param vers.otel %}}`                       | OkHttp based implementation.                              | `grpc`, `http/protobuf` | Yes     |
| `io.opentelemetry:opentelemetry-exporter-sender-jdk:{{% param vers.otel %}}`                          | Java 11+ `java.net.http.HttpClient` based implementation. | `http/protobuf`         | No      |
| `io.opentelemetry:opentelemetry-exporter-sender-grpc-managed-channel:{{% param vers.otel %}}` **[1]** | `grpc-java` `ManagedChannel` based implementation.        | `grpc`                  | No      |

**[1]**: In order to use `opentelemetry-exporter-sender-grpc-managed-channel`,
you must also add a dependency on a
[gRPC transport implementations](https://github.com/grpc/grpc-java#transport).

#### Authentication

The OTLP exporters provide mechanisms for static and dynamic header-based
authentication, and for mTLS.

If using
[zero-code SDK autoconfigure](../configuration/#zero-code-sdk-autoconfigure)
with environment variables and system properties, see
[relevant system properties](../configuration/#properties-exporters):

- `otel.exporter.otlp.headers` for static header-based authentication.
- `otel.exporter.otlp.client.key`, `otel.exporter.otlp.client.certificate` for
  mTLS authentication.

The following code snippet demonstrates programmatic configuration of static and
dynamic header-based authentication:


<?code-excerpt "src/main/java/otel/OtlpAuthenticationConfig.java"?>
```java
package otel;

import io.opentelemetry.exporter.otlp.http.logs.OtlpHttpLogRecordExporter;
import io.opentelemetry.exporter.otlp.http.metrics.OtlpHttpMetricExporter;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import java.time.Duration;
import java.time.Instant;
import java.util.Collections;
import java.util.Map;
import java.util.function.Supplier;

public class OtlpAuthenticationConfig {
  public static void staticAuthenticationHeader(String endpoint) {
    // If the OTLP destination accepts a static, long-lived authentication header like an API key,
    // set it as a header.
    // This reads the API key from the OTLP_API_KEY env var to avoid hard coding the secret in
    // source code.
    String apiKeyHeaderName = "api-key";
    String apiKeyHeaderValue = System.getenv("OTLP_API_KEY");

    // Initialize OTLP Span, Metric, and LogRecord exporters using a similar pattern
    OtlpHttpSpanExporter spanExporter =
        OtlpHttpSpanExporter.builder()
            .setEndpoint(endpoint)
            .addHeader(apiKeyHeaderName, apiKeyHeaderValue)
            .build();
    OtlpHttpMetricExporter metricExporter =
        OtlpHttpMetricExporter.builder()
            .setEndpoint(endpoint)
            .addHeader(apiKeyHeaderName, apiKeyHeaderValue)
            .build();
    OtlpHttpLogRecordExporter logRecordExporter =
        OtlpHttpLogRecordExporter.builder()
            .setEndpoint(endpoint)
            .addHeader(apiKeyHeaderName, apiKeyHeaderValue)
            .build();
  }

  public static void dynamicAuthenticationHeader(String endpoint) {
    // If the OTLP destination requires a dynamic authentication header, such as a JWT which needs
    // to be periodically refreshed, use a header supplier.
    // Here we implement a simple supplier which adds a header of the form "Authorization: Bearer
    // <token>", where <token> is fetched from refreshBearerToken every 10 minutes.
    String username = System.getenv("OTLP_USERNAME");
    String password = System.getenv("OTLP_PASSWORD");
    Supplier<Map<String, String>> supplier =
        new AuthHeaderSupplier(() -> refreshToken(username, password), Duration.ofMinutes(10));

    // Initialize OTLP Span, Metric, and LogRecord exporters using a similar pattern
    OtlpHttpSpanExporter spanExporter =
        OtlpHttpSpanExporter.builder().setEndpoint(endpoint).setHeaders(supplier).build();
    OtlpHttpMetricExporter metricExporter =
        OtlpHttpMetricExporter.builder().setEndpoint(endpoint).setHeaders(supplier).build();
    OtlpHttpLogRecordExporter logRecordExporter =
        OtlpHttpLogRecordExporter.builder().setEndpoint(endpoint).setHeaders(supplier).build();
  }

  private static class AuthHeaderSupplier implements Supplier<Map<String, String>> {
    private final Supplier<String> tokenRefresher;
    private final Duration tokenRefreshInterval;
    private Instant refreshedAt = Instant.ofEpochMilli(0);
    private String currentTokenValue;

    private AuthHeaderSupplier(Supplier<String> tokenRefresher, Duration tokenRefreshInterval) {
      this.tokenRefresher = tokenRefresher;
      this.tokenRefreshInterval = tokenRefreshInterval;
    }

    @Override
    public Map<String, String> get() {
      return Collections.singletonMap("Authorization", "Bearer " + getToken());
    }

    private synchronized String getToken() {
      Instant now = Instant.now();
      if (currentTokenValue == null || now.isAfter(refreshedAt.plus(tokenRefreshInterval))) {
        currentTokenValue = tokenRefresher.get();
        refreshedAt = now;
      }
      return currentTokenValue;
    }
  }

  private static String refreshToken(String username, String password) {
    // For a production scenario, this would be replaced with an out-of-band request to exchange
    // username / password for bearer token.
    return "abc123";
  }
}
```


### Testing

TODO: document tools available for testing the SDK

[JSON file encoding]:
  /docs/specs/otel/protocol/file-exporter/#json-file-serialization