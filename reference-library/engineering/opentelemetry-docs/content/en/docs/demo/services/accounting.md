---
title: "Accounting Service"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

This service calculates the total amount of sold products. This calculation is
currently mocked and received orders are printed out. Once a record is retrieved
from Kafka, it is saved to the database (PostgreSQL).

[Accounting Service](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/accounting/)

## Auto-instrumentation

This service relies on the OpenTelemetry .NET Automatic Instrumentation to
automatically instrument libraries such as Kafka, and to configure the
OpenTelemetry SDK. The instrumentation is added via Nuget package
[OpenTelemetry.AutoInstrumentation](https://www.nuget.org/packages/OpenTelemetry.AutoInstrumentation)
and activated using environment variables that are sourced from `instrument.sh`.
Using this installation approach also guarantees that all instrumentation
dependencies are properly aligned with the application.

## Publishing

Add `--use-current-runtime` to the `dotnet publish` command to distribute
appropriate native runtime components.

```sh
dotnet publish "./AccountingService.csproj" --use-current-runtime -c $BUILD_CONFIGURATION -o /app/publish /p:UseAppHost=false
```
