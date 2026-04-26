## Global settings

| Environment variable                 | Description                                                                                                                                                                                                                             | Default value | Status                                                    |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | --------------------------------------------------------- |
| `OTEL_DOTNET_AUTO_HOME`              | Installation location.                                                                                                                                                                                                                  |               | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `OTEL_DOTNET_AUTO_EXCLUDE_PROCESSES` | Names of the executable files that the profiler cannot instrument. Supports multiple comma-separated values, for example: `ReservedProcess.exe,powershell.exe`. If unset, the profiler attaches to all processes by default. \[1\]\[2\] |               | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `OTEL_DOTNET_AUTO_FAIL_FAST_ENABLED` | Enables possibility to fail process when automatic instrumentation cannot be executed. It is designed for debugging purposes. It should not be used in production environment. \[1\]                                                    | `false`       | [Experimental](/docs/specs/otel/versioning-and-stability) |

\[1\] If `OTEL_DOTNET_AUTO_FAIL_FAST_ENABLED` is set to `true` then processes
excluded from instrumentation by `OTEL_DOTNET_AUTO_EXCLUDE_PROCESSES` will fail
instead of silently continue.

\[2\] Notice that applications launched via `dotnet MyApp.dll` have process name
`dotnet` or `dotnet.exe`.