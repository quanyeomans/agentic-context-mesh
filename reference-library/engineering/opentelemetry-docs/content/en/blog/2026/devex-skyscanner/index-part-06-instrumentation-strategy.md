## Instrumentation strategy

Skyscanner's Java-heavy environment benefits significantly from OpenTelemetry's
auto-instrumentation capabilities. The Java agent, pre-configured in base Docker
images, provides HTTP and gRPC span generation out of the box.

### Opinionated auto-instrumentation

The team takes a deliberately opinionated approach to auto-instrumentation.
Rather than enabling everything by default, they start from the opposite
direction: all instrumentations are disabled in a shared base Docker image, and
only a curated set is explicitly enabled.

> "It's sort of the other way around. We disable everything, then enable what we
> need," Neil explained.

Using environment variables in the base image, Skyscanner enables a focused set
of runtime-, HTTP-, and gRPC-related instrumentations by default. This includes
JAX-RS, gRPC, Jetty, common HTTP clients, executor instrumentation, and logging
context propagation. Service teams inherit these defaults automatically but
remain free to override them or enable additional instrumentations in their own
service definitions if needed.

This model ensures consistency across hundreds of services while still allowing
flexibility at the edges.

### Setting up the Java agent

The snippet below is an illustration of the shared Java base image. It bundles
the OpenTelemetry Java agent into the image, sets organization-wide defaults,
and installs a common launcher script:

```Dockerfile base image