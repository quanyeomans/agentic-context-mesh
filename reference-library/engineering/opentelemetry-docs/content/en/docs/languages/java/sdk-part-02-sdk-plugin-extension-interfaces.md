## SDK plugin extension interfaces

When built-in components are insufficient, the SDK can be extended by
implementing various plugin extension interfaces:

- [Sampler](#sampler): Configures which spans are recorded and sampled.
- [SpanProcessor](#spanprocessor): Processes spans when they start and end.
- [SpanExporter](#spanexporter): Exports spans out of process.
- [MetricReader](#metricreader): Reads aggregated metrics.
- [MetricExporter](#metricexporter): Exports metrics out of process.
- [LogRecordProcessor](#logrecordprocessor): Processes log records when they are
  emitted.
- [LogRecordExporter](#logrecordexporter): Exports log records out of process.
- [TextMapPropagator](#textmappropagator): Propagates context across process
  boundaries.