## Architecture: centralized routing, distributed collection

Skyscanner's collector architecture features a central DNS endpoint with
Istio-based intelligent routing. Regardless of where services run globally or
which cluster they're in, they send telemetry to this single address. Istio
handles routing requests to the nearest available collector.

The deployment consists of two distinct collector patterns:

**Gateway Collector (Replica Set)**: Handles bulk OTLP traffic (traces and
metrics) from most services, where the majority of processing happens.

**Agent Collector (DaemonSet)**: Scrapes Prometheus endpoints from open source
and platform services that don't yet support OTLP natively.

![Skyscanner architecture diagram](skyscanner-architecture.png)