## Incubating API

The `io.opentelemetry:opentelemetry-api-incubator:{{% param vers.otel %}}-alpha`
artifact contains experimental trace, metric, log, and context APIs which.
Incubating APIs may have breaking API changes in minor releases. Often, these
represent experimental specification features or API designs we want to vet with
user feedback before committing to. We encourage users to try these APIs and
open issues with any feedback (positive or negative). Libraries should not
depend on the incubating APIs, since users may be exposed to runtime errors when
transitive version conflicts occur.

See
[incubator README](https://github.com/open-telemetry/opentelemetry-java/tree/main/api/incubator)
for available APIs and sample usage.