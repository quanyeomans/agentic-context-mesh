## Internal logs

The default directory paths for internal logs are:

- Windows: `%ProgramData%\OpenTelemetry .NET AutoInstrumentation\logs`
- Linux: `/var/log/opentelemetry/dotnet`
- macOS: `/var/log/opentelemetry/dotnet`

If the default log directories can't be created, the instrumentation uses the
path of the current user's
[temporary folder](https://docs.microsoft.com/en-us/dotnet/api/System.IO.Path.GetTempPath?view=net-6.0)
instead.

| Environment variable             | Description                                                                           | Default value                            | Status                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------- | ---------------------------------------- | --------------------------------------------------------- |
| `OTEL_DOTNET_AUTO_LOG_DIRECTORY` | Directory of the .NET Tracer logs.                                                    | _See the previous note on default paths_ | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `OTEL_LOG_LEVEL`                 | SDK log level. (supported values: `none`,`error`,`warn`,`info`,`debug`)               | `info`                                   | [Stable](/docs/specs/otel/versioning-and-stability)       |
| `OTEL_DOTNET_AUTO_LOGGER`        | AutoInstrumentation diagnostic logs sink. (supported values: `none`,`file`,`console`) | `file`                                   | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `OTEL_DOTNET_AUTO_LOG_FILE_SIZE` | Maximum size (in bytes) of a single log file created by the Auto Instrumentation      | 10 485 760 (10 MB)                       | [Experimental](/docs/specs/otel/versioning-and-stability) |