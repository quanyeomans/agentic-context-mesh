## .NET CLR Profiler

The CLR uses the following environment variables to set up the profiler. See
[.NET Runtime Profiler Loading](https://github.com/dotnet/runtime/blob/d8302cef7946be82775ba5b94a88ad8eee800714/docs/design/coreclr/profiling/Profiler%20Loading.md)
for more information.

| .NET Framework environment variable | .NET environment variable  | Description                                                                             | Required value                                                                                                                                                                                                                                                    | Status                                                    |
| ----------------------------------- | -------------------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `COR_ENABLE_PROFILING`              | `CORECLR_ENABLE_PROFILING` | Enables the profiler.                                                                   | `1`                                                                                                                                                                                                                                                               | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `COR_PROFILER`                      | `CORECLR_PROFILER`         | CLSID of the profiler.                                                                  | `{918728DD-259F-4A6A-AC2B-B85E1B658318}`                                                                                                                                                                                                                          | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `COR_PROFILER_PATH`                 | `CORECLR_PROFILER_PATH`    | Path to the profiler.                                                                   | `$INSTALL_DIR/linux-x64/OpenTelemetry.AutoInstrumentation.Native.so` for Linux glibc, `$INSTALL_DIR/linux-musl-x64/OpenTelemetry.AutoInstrumentation.Native.so` for Linux musl, `$INSTALL_DIR/osx-arm64/OpenTelemetry.AutoInstrumentation.Native.dylib` for macOS | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `COR_PROFILER_PATH_32`              | `CORECLR_PROFILER_PATH_32` | Path to the 32-bit profiler. Bitness-specific paths take precedence over generic paths. | `$INSTALL_DIR/win-x86/OpenTelemetry.AutoInstrumentation.Native.dll` for Windows                                                                                                                                                                                   | [Experimental](/docs/specs/otel/versioning-and-stability) |
| `COR_PROFILER_PATH_64`              | `CORECLR_PROFILER_PATH_64` | Path to the 64-bit profiler. Bitness-specific paths take precedence over generic paths. | `$INSTALL_DIR/win-x64/OpenTelemetry.AutoInstrumentation.Native.dll` for Windows                                                                                                                                                                                   | [Experimental](/docs/specs/otel/versioning-and-stability) |

Setting OpenTelemetry .NET Automatic Instrumentation as a .NET CLR Profiler is
required for .NET Framework.

On .NET, the .NET CLR Profiler is used only for bytecode instrumentation. If
having just source instrumentation is acceptable, you can unset or remove the
following environment variables:

```env
COR_ENABLE_PROFILING
COR_PROFILER
COR_PROFILER_PATH_32
COR_PROFILER_PATH_64
CORECLR_ENABLE_PROFILING
CORECLR_PROFILER
CORECLR_PROFILER_PATH
CORECLR_PROFILER_PATH_32
CORECLR_PROFILER_PATH_64
```