## Configuration methods

You can apply or edit configuration settings in the following ways, with
environment variables taking precedence over `App.config` or `Web.config` file:

1. Environment variables

   Environment variables are the main way to configure the settings.

2. `App.config` or `Web.config` file

   For an application running on .NET Framework, you can use a web configuration
   file (`web.config`) or an application configuration file (`app.config`) to
   configure the `OTEL_*` settings.

   ⚠️ Only settings starting with `OTEL_` can be set using `App.config` or
   `Web.config`. However, the following settings are not supported:
   - `OTEL_DOTNET_AUTO_HOME`
   - `OTEL_DOTNET_AUTO_EXCLUDE_PROCESSES`
   - `OTEL_DOTNET_AUTO_FAIL_FAST_ENABLED`
   - `OTEL_DOTNET_AUTO_[TRACES|METRICS|LOGS]_INSTRUMENTATION_ENABLED`
   - `OTEL_DOTNET_AUTO_[TRACES|METRICS|LOGS]_{INSTRUMENTATION_ID}_INSTRUMENTATION_ENABLED`
   - `OTEL_DOTNET_AUTO_LOG_DIRECTORY`
   - `OTEL_LOG_LEVEL`
   - `OTEL_DOTNET_AUTO_NETFX_REDIRECT_ENABLED` (Deprecated)
   - `OTEL_DOTNET_AUTO_REDIRECT_ENABLED`
   - `OTEL_DOTNET_AUTO_SQLCLIENT_NETFX_ILREWRITE_ENABLED`

   Example with `OTEL_SERVICE_NAME` setting:

   ```xml
   <configuration>
   <appSettings>
       <add key="OTEL_SERVICE_NAME" value="my-service-name" />
   </appSettings>
   </configuration>
   ```

   > [!NOTE]
   >
   > On .NET Framework, `OTEL_*` values from `Web.config` or `App.config` are
   > promoted to process-level environment variables at startup, and the OTel
   > SDK is initialized only once per process. In IIS, where multiple
   > applications can share a single worker process (Application Pool), this
   > means the first application to start determines the configuration for all
   > applications in that pool.

3. Service name automatic detection

   If no service name is explicitly configured one will be generated for you.
   This can be helpful in some circumstances.
   - If the application is hosted on IIS in .NET Framework this will be
     `SiteName\VirtualPath` ex: `MySite\MyApp`
   - If that is not the case it will use the name of the application
     [entry Assembly](https://learn.microsoft.com/en-us/dotnet/api/system.reflection.assembly.getentryassembly?view=net-7.0).

By default we recommend using environment variables for configuration. However,
if given setting supports it, then:

- use `Web.config` for configuring an ASP.NET application (.NET Framework),
- use `App.config` for configuring a Windows Service (.NET Framework).