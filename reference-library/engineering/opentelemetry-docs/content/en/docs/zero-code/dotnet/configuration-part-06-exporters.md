## Exporters

Exporters output the telemetry.

| Environment variable    | Description                                                                                    | Default value | Status                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------- | ------------- | --------------------------------------------------- |
| `OTEL_TRACES_EXPORTER`  | Comma-separated list of exporters. Supported options: `otlp`, `zipkin` [1], `console`, `none`. | `otlp`        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_METRICS_EXPORTER` | Comma-separated list of exporters. Supported options: `otlp`, `prometheus`, `console`, `none`. | `otlp`        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_LOGS_EXPORTER`    | Comma-separated list of exporters. Supported options: `otlp`, `console`, `none`.               | `otlp`        | [Stable](/docs/specs/otel/versioning-and-stability) |

**[1]**: `zipkin` is deprecated and will be removed in the upcoming release.

### Traces exporter

| Environment variable             | Description                                                                  | Default value | Status                                              |
| -------------------------------- | ---------------------------------------------------------------------------- | ------------- | --------------------------------------------------- |
| `OTEL_BSP_SCHEDULE_DELAY`        | Delay interval (in milliseconds) between two consecutive exports.            | `5000`        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_BSP_EXPORT_TIMEOUT`        | Maximum allowed time (in milliseconds) to export data                        | `30000`       | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_BSP_MAX_QUEUE_SIZE`        | Maximum queue size.                                                          | `2048`        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` | Maximum batch size. Must be less than or equal to `OTEL_BSP_MAX_QUEUE_SIZE`. | `512`         | [Stable](/docs/specs/otel/versioning-and-stability) |

### Metrics exporter

| Environment variable          | Description                                                                   | Default value                                           | Status                                              |
| ----------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------- |
| `OTEL_METRIC_EXPORT_INTERVAL` | The time interval (in milliseconds) between the start of two export attempts. | `60000` for OTLP exporter, `10000` for console exporter | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_METRIC_EXPORT_TIMEOUT`  | Maximum allowed time (in milliseconds) to export data.                        | `30000` for OTLP exporter, none for console exporter    | [Stable](/docs/specs/otel/versioning-and-stability) |

### Logs exporter

| Environment variable                              | Description                                             | Default value | Status                                                    |
| ------------------------------------------------- | ------------------------------------------------------- | ------------- | --------------------------------------------------------- |
| `OTEL_DOTNET_AUTO_LOGS_INCLUDE_FORMATTED_MESSAGE` | Whether the formatted log message should be set or not. | `false`       | [Experimental](/docs/specs/otel/versioning-and-stability) |

### OTLP

**Status**: [Stable](/docs/specs/otel/versioning-and-stability)

To enable the OTLP exporter, set the
`OTEL_TRACES_EXPORTER`/`OTEL_METRICS_EXPORTER`/`OTEL_LOGS_EXPORTER` environment
variable to `otlp`.

To customize the OTLP exporter using environment variables, see the
[OTLP exporter documentation](https://github.com/open-telemetry/opentelemetry-dotnet/tree/core-1.15.0/src/OpenTelemetry.Exporter.OpenTelemetryProtocol#environment-variables).
Important environment variables include:

| Environment variable                                | Description                                                                                                                                                                                    | Default value                                                                        | Status                                              |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | --------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT`                       | Target endpoint for the OTLP exporter. See [the OpenTelemetry specification](/docs/specs/otel/protocol/exporter/) for more details.                                                            | `http/protobuf`: `http://localhost:4318`, `grpc`: `http://localhost:4317`            | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`                | Equivalent to `OTEL_EXPORTER_OTLP_ENDPOINT`, but applies only to traces.                                                                                                                       | `http/protobuf`: `http://localhost:4318/v1/traces`, `grpc`: `http://localhost:4317`  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`               | Equivalent to `OTEL_EXPORTER_OTLP_ENDPOINT`, but applies only to metrics.                                                                                                                      | `http/protobuf`: `http://localhost:4318/v1/metrics`, `grpc`: `http://localhost:4317` | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`                  | Equivalent to `OTEL_EXPORTER_OTLP_ENDPOINT`, but applies only to logs.                                                                                                                         | `http/protobuf`: `http://localhost:4318/v1/logs`, `grpc`: `http://localhost:4317`    | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_PROTOCOL`                       | OTLP exporter transport protocol. Supported values are `grpc`, `http/protobuf`. [1]                                                                                                            | `http/protobuf`                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL`                | Equivalent to `OTEL_EXPORTER_OTLP_PROTOCOL`, but applies only to traces.                                                                                                                       | `http/protobuf`                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_METRICS_PROTOCOL`               | Equivalent to `OTEL_EXPORTER_OTLP_PROTOCOL`, but applies only to metrics.                                                                                                                      | `http/protobuf`                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL`                  | Equivalent to `OTEL_EXPORTER_OTLP_PROTOCOL`, but applies only to logs.                                                                                                                         | `http/protobuf`                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_TIMEOUT`                        | The max waiting time (in milliseconds) for the backend to process each batch.                                                                                                                  | `10000` (10s)                                                                        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT`                 | Equivalent to `OTEL_EXPORTER_OTLP_TIMEOUT`, but applies only to traces.                                                                                                                        | `10000` (10s)                                                                        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_METRICS_TIMEOUT`                | Equivalent to `OTEL_EXPORTER_OTLP_TIMEOUT`, but applies only to metrics.                                                                                                                       | `10000` (10s)                                                                        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_LOGS_TIMEOUT`                   | Equivalent to `OTEL_EXPORTER_OTLP_TIMEOUT`, but applies only to logs.                                                                                                                          | `10000` (10s)                                                                        | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_HEADERS`                        | Comma-separated list of additional HTTP headers sent with each export, for example: `Authorization=secret,X-Key=Value`.                                                                        |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_TRACES_HEADERS`                 | Equivalent to `OTEL_EXPORTER_OTLP_HEADERS`, but applies only to traces.                                                                                                                        |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_METRICS_HEADERS`                | Equivalent to `OTEL_EXPORTER_OTLP_HEADERS`, but applies only to metrics.                                                                                                                       |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_LOGS_HEADERS`                   | Equivalent to `OTEL_EXPORTER_OTLP_HEADERS`, but applies only to logs.                                                                                                                          |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_CERTIFICATE`                    | Path to the CA certificate file (PEM format) used to verify the server's TLS certificate. \[3\]                                                                                                |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE`             | Path to the client certificate file (PEM format) for mTLS authentication. \[3\]                                                                                                                |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_CLIENT_KEY`                     | Path to the client private key file (PEM format) for mTLS authentication. \[3\]                                                                                                                |                                                                                      | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`                 | Maximum allowed attribute value size.                                                                                                                                                          | none                                                                                 | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_ATTRIBUTE_COUNT_LIMIT`                        | Maximum allowed span attribute count.                                                                                                                                                          | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT`            | Maximum allowed attribute value size. [Not applicable for metrics.](https://github.com/open-telemetry/opentelemetry-specification/blob/v1.15.0/specification/metrics/sdk.md#attribute-limits). | none                                                                                 | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT`                   | Maximum allowed span attribute count. [Not applicable for metrics.](https://github.com/open-telemetry/opentelemetry-specification/blob/v1.15.0/specification/metrics/sdk.md#attribute-limits). | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_SPAN_EVENT_COUNT_LIMIT`                       | Maximum allowed span event count.                                                                                                                                                              | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_SPAN_LINK_COUNT_LIMIT`                        | Maximum allowed span link count.                                                                                                                                                               | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EVENT_ATTRIBUTE_COUNT_LIMIT`                  | Maximum allowed attribute per span event count.                                                                                                                                                | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_LINK_ATTRIBUTE_COUNT_LIMIT`                   | Maximum allowed attribute per span link count.                                                                                                                                                 | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT`       | Maximum allowed log record attribute value size.                                                                                                                                               | none                                                                                 | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT`              | Maximum allowed log record attribute count.                                                                                                                                                    | 128                                                                                  | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | The aggregation temporality to use on the basis of instrument kind. [2]                                                                                                                        | `cumulative`                                                                         | [Stable](/docs/specs/otel/versioning-and-stability) |

**[1]**: Considerations on the `OTEL_EXPORTER_OTLP_PROTOCOL`:

- The OpenTelemetry .NET Automatic Instrumentation defaults to `http/protobuf`,
  which differs from the OpenTelemetry .NET SDK default value of `grpc`.
- On .NET 8 and higher, the application must reference
  [`Grpc.Net.Client`](https://www.nuget.org/packages/Grpc.Net.Client/) to use
  the `grpc` OTLP exporter protocol. For example, by adding
  `<PackageReference Include="Grpc.Net.Client" Version="2.65.0" />` to the
  `.csproj` file.
- On .NET Framework, the `grpc` OTLP exporter protocol is not supported.

**[2]**: The recognized (case-insensitive) values for
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` are:

- `Cumulative`: Choose cumulative aggregation temporality for all instrument
  kinds.
- `Delta`: Choose Delta aggregation temporality for Counter, Asynchronous
  Counter and Histogram instrument kinds, choose Cumulative aggregation for
  UpDownCounter and Asynchronous UpDownCounter instrument kinds.
- `LowMemory`: This configuration uses Delta aggregation temporality for
  Synchronous Counter and Histogram and uses Cumulative aggregation temporality
  for Synchronous UpDownCounter, Asynchronous Counter, and Asynchronous
  UpDownCounter instrument kinds.
  - ⚠️ This value known from
    [specification](https://github.com/open-telemetry/opentelemetry-specification/blob/v1.35.0/specification/metrics/sdk_exporters/otlp.md?plain=1#L48)
    is not supported.

**[3]**: Considerations on mTLS (mutual TLS) configuration:

- mTLS is only supported on .NET 8.0 and higher.
- All certificate files must be in PEM format.
- When using mTLS, the `OTEL_EXPORTER_OTLP_ENDPOINT` must use `https://`.
- mTLS is not supported on .NET Framework.

### Prometheus

**Status**: [Experimental](/docs/specs/otel/versioning-and-stability)

> [!WARNING] Warning: **do NOT use in production**
>
> Prometheus exporter is intended for the inner dev loop. Production
> environments can use a combination of OTLP exporter with
> [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector-releases)
> having
> [`otlp` receiver](https://github.com/open-telemetry/opentelemetry-collector/tree/v0.97.0/receiver/otlpreceiver)
> and
> [`prometheus` exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.97.0/exporter/prometheusexporter).

To enable the Prometheus exporter, set the `OTEL_METRICS_EXPORTER` environment
variable to `prometheus`.

The exporter exposes the metrics HTTP endpoint on
`http://localhost:9464/metrics` and it caches the responses for 300
milliseconds.

See the
[Prometheus Exporter HttpListener documentation](https://github.com/open-telemetry/opentelemetry-dotnet/tree/coreunstable-1.15.0-beta.1/src/OpenTelemetry.Exporter.Prometheus.HttpListener).
to learn more.

### Zipkin

**Status**: [Stable](/docs/specs/otel/versioning-and-stability)

To enable the Zipkin exporter, set the `OTEL_TRACES_EXPORTER` environment
variable to `zipkin`.

To customize the Zipkin exporter using environment variables, see the
[Zipkin exporter documentation](https://github.com/open-telemetry/opentelemetry-dotnet/tree/core-1.15.0/src/OpenTelemetry.Exporter.Zipkin#configuration-using-environment-variables).
Important environment variables include:

| Environment variable            | Description | Default value                        | Status                                              |
| ------------------------------- | ----------- | ------------------------------------ | --------------------------------------------------- |
| `OTEL_EXPORTER_ZIPKIN_ENDPOINT` | Zipkin URL  | `http://localhost:9411/api/v2/spans` | [Stable](/docs/specs/otel/versioning-and-stability) |