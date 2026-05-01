## RuleEngine

RuleEngine is a feature that validates OpenTelemetry API, SDK, Instrumentation,
and Exporter assemblies for unsupported scenarios, ensuring that OpenTelemetry
automatic instrumentation is more stable by backing of instead of crashing. It
works on .NET 8 and higher.

Enable RuleEngine only during the first run of the application, or when the
deployment changes or the Automatic Instrumentation library is upgraded. Once
validated, there's no need to revalidate the rules when the application
restarts.

| Environment variable                   | Description         | Default value | Status                                                    |
| -------------------------------------- | ------------------- | ------------- | --------------------------------------------------------- |
| `OTEL_DOTNET_AUTO_RULE_ENGINE_ENABLED` | Enables RuleEngine. | `true`        | [Experimental](/docs/specs/otel/versioning-and-stability) |