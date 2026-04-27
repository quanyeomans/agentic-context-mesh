## Propagators

Propagators allow applications to share context. See
[the OpenTelemetry specification](/docs/specs/otel/context/api-propagators) for
more details.

| Environment variable | Description                                                                                                                                                                                                                                                                                                  | Default value          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- |
| `OTEL_PROPAGATORS`   | Comma-separated list of propagators. Supported options: `tracecontext`, `baggage`, `b3multi`, `b3`. See [the OpenTelemetry specification](https://github.com/open-telemetry/opentelemetry-specification/blob/v1.14.0/specification/sdk-environment-variables.md#general-sdk-configuration) for more details. | `tracecontext,baggage` |