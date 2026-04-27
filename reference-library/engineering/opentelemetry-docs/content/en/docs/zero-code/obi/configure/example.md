---
title: "OBI configuration YAML example"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

## YAML file example

```yaml
discovery:
  instrument:
    - open_ports: 8443
log_level: DEBUG

ebpf:
  context_propagation: all

otel_traces_export:
  endpoint: http://localhost:4318

prometheus_export:
  port: 8999
  path: /metrics
```

This configuration includes the following options:

- `discovery.instrument.open_ports`: instruments services listening on port 8443
- `log_level`: sets logging verbosity to `DEBUG`
- `ebpf.context_propagation`: enables context propagation using all supported
  carriers
- `otel_traces_export.endpoint`: sends traces to the OpenTelemetry Collector at
  `http://localhost:4318`
- `prometheus_export`: exposes metrics at `http://localhost:8999/metrics`
