## .NET Runtime

On .NET it is required to set the
[`DOTNET_STARTUP_HOOKS`](https://github.com/dotnet/runtime/blob/main/docs/design/features/host-startup-hook.md)
environment variable if the .NET CLR Profiler is not used.

The
[`DOTNET_ADDITIONAL_DEPS`](https://github.com/dotnet/runtime/blob/main/docs/design/features/additional-deps.md)
and
[`DOTNET_SHARED_STORE`](https://docs.microsoft.com/en-us/dotnet/core/deploying/runtime-store)
environment variable are used to mitigate assembly version conflicts in .NET.

| Environment variable     | Required value                                                       | Status                                                    |
| ------------------------ | -------------------------------------------------------------------- | --------------------------------------------------------- |
| `DOTNET_STARTUP_HOOKS`   | `$INSTALL_DIR/net/OpenTelemetry.AutoInstrumentation.StartupHook.dll` | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `DOTNET_ADDITIONAL_DEPS` | `$INSTALL_DIR/AdditionalDeps`                                        | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `DOTNET_SHARED_STORE`    | `$INSTALL_DIR/store`                                                 | [Experimental](/docs/specs/otel/versioning-and-stability) |

If the .NET CLR Profiler is used and the
[`DOTNET_STARTUP_HOOKS`](https://github.com/dotnet/runtime/blob/main/docs/design/features/host-startup-hook.md)
environment variable is not set, the profiler looks for
`OpenTelemetry.AutoInstrumentation.StartupHook.dll` in an appropriate directory
relative to the `OpenTelemetry.AutoInstrumentation.Native.dll` file location.
The folder structure can match the ZIP archive structure or the NuGet package
structure (either platform dependent or independent). If the startup hook
assembly is not found, the profiler loading will be aborted.