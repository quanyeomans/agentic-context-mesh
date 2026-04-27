## Configuration: start simple, evolve gradually

When Skyscanner first deployed collectors in 2021, their configuration was
minimal: memory limiter, batch processor, and an OTLP exporter for traces.

Over time, the configuration evolved organically: adding metrics pipelines,
integrating Istio span ingestion, implementing span-to-metrics transformation,
and adding filter processors to reduce noise and control costs.

### Turning Istio service mesh spans into platform metrics

One of Skyscanner's most innovative uses of the collector involves generating
metrics from Istio service mesh spans.

Istio's native metrics suffered from cardinality explosion issues that would
overwhelm their Prometheus deployment. Additionally, Skyscanner operates many
off-the-shelf services where they don't own the code but still need consistent
metrics.

Their solution: Configure Istio to emit spans (originally in Zipkin format,
though Istio now supports OTLP), ingest them through the collector with the
Zipkin receiver, transform them to meet semantic conventions, and use the
[span metrics connector](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/e8a502371ea1d2c3534235d623c1b1eb3b6b4b58/connector/spanmetricsconnector?from_branch=main)
to generate consistent metrics without any application instrumentation.

> "We can do that at a platform level without the application owners having to
> instrument their code at all," Neil noted.

The span metrics connector configuration extracts key dimensions from the spans:

```yaml
connectors:
  spanmetrics:
    aggregation_temporality: AGGREGATION_TEMPORALITY_DELTA
    dimensions:
      - name: http.status_code
      - name: grpc.status_code
      - name: rpc.service
      - name: rpc.method
      - name: prot
      - name: flag
      - name: k8s.deployment.name
      - name: k8s.replicaset.name
      - name: destination_subset
    dimensions_cache_size: 15000000
    histogram:
      exponential:
        max_size: 160
      unit: ms
    metrics_flush_interval: 30s
```

The collector then transforms these metrics to use semantic convention names
like `http.client.duration` and `http.server.duration`, aggregating them by
cluster, service name, and HTTP status code. This provides platform-level HTTP
metrics for every service without code changes, consistent naming adhering to
semantic conventions, and lower cardinality than native Istio metrics.

### The 404 error challenge

One notable challenge with the collector configuration involved cache services
that returned HTTP 404 to indicate that an entry did not exist in the cache. The
collector treated these 404s as errors, triggering 100% trace sampling for what
was actually normal, high-volume behavior.

The solution was adding a filter processor to unset the error status for these
specific 404 responses:

```yaml
processors:
  span/unset_cache_client_404:
    include:
      attributes:
        - key: http.response.status_code
          value: ^404$
        - key: server.address
          value: ^(service-x\.skyscanner\.net|service-y\.skyscanner\.net|service-z\.skyscanner\.net|service-z-\w{2}-\w+-\d\.int\.\w{2}-\w+-\d\.skyscanner\.com)$
      match_type: regexp
      regexp:
        cacheenabled: true
        cachemaxnumentries: 1000
    status:
      code: Unset
```

This processor matches spans with 404 status codes from specific cache services
and unsets their error status, preventing them from triggering error-based
sampling.

> "We'd have had higher-quality, easier-to-use traces if we had that filter
> processor from the start," Neil reflected.

However, Neil notes that with the recent introduction of OpenTelemetry SDK
[declarative configuration](/docs/languages/sdk-configuration/declarative-configuration/),
such filtering could now be configured in a decentralized fashion by the service
teams themselves, rather than requiring changes to the central collector
configuration.

### Configuration deep dive

Skyscanner has shared their production collector configurations to help others
understand these patterns in practice:

#### Gateway collector

The [gateway collector][gateway-otelbin] handles the bulk of processing:

- Receives OTLP metrics and traces from services and Zipkin spans from Istio
- Uses the span metrics connector to generate metrics from Istio spans
- Employs extensive transform processors to map Istio attributes to semantic
  conventions
- Implements the 404 filtering logic for cache services
- Exports metrics and traces to the observability vendor via OTLP

The diagram illustrates how OTLP metrics and traces, as well as Istio spans,
reach these gateway collectors:

![Skyscanner architecture (Gateway Collector) diagram](skyscanner-architecture-gateway.png)

#### Agent collector

The [agent collector][agent-otelbin] focuses on collecting infrastructure and
platform-level metrics from each node:

- Scrapes Prometheus endpoints from various sources (node exporter,
  kube-state-metrics, kubelet)
- Performs minimal processing (memory limiting, batching, attribute cleanup)
- Exports metrics to the observability vendor via OTLP