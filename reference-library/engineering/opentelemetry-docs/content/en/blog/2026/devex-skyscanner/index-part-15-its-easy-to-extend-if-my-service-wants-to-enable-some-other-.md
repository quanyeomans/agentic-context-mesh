# It's easy to extend if my-service wants to enable some other non-default instrumentation
ENV OTEL_INSTRUMENTATION_OPENAI_ENABLED=true
ENV OTEL_INSTRUMENTATION_OKHTTP_ENABLED=true

CMD exec /usr/bin/run.sh -jar my-service.jar server
```

### Spans yes, metrics no (by default)

A particularly interesting aspect of Skyscanner's strategy is how they treat
metrics versus traces. Although HTTP and gRPC instrumentations are enabled, the
team deliberately drops most SDK-generated HTTP and RPC metrics. This is because
they already derive consistent, lower-cardinality platform metrics from Istio
service mesh spans, as described earlier.

Rather than disabling the instrumentations entirely—which would also remove
spans—they use OpenTelemetry SDK views to drop the metric aggregations while
preserving tracing:

- HTTP and RPC metrics are dropped globally
- Spans continue to be emitted as normal
- Service teams can selectively re-enable specific metrics (for example,
  server-side latency) if they need additional granularity beyond what Istio
  provides

When teams do opt back into SDK metrics, they often rename them to avoid
clashing or double-counting with existing Istio-derived metrics.

In the [Java base image](#setting-up-the-java-agent) shown earlier,
`OTEL_EXPERIMENTAL_METRICS_VIEW_CONFIG` points to Skyscanner's default
`otel-view.yaml`, using
[view file configuration](https://github.com/open-telemetry/opentelemetry-java/blob/65f7412a986cb474314b093c1bbba77955b52031/sdk-extensions/incubator/README.md#view-file-configuration):

```yaml