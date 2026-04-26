## Programmatic configuration

The programmatic configuration interface is the set of APIs for constructing
[SDK](../sdk/) components. All SDK components have a programmatic configuration
API, and all other configuration mechanisms are built on top of this API. For
example, the
[autoconfigure environment variable and system property](#environment-variables-and-system-properties)
configuration interface interprets well-known environment variables and system
properties into a series of calls to the programmatic configuration API.

While other configuration mechanisms offer more convenience, none offer the
flexibility of writing code expressing the precise configuration required. When
a particular capability isn't supported by a higher order configuration
mechanism, you might have no choice but to use programmatic configuration.

The [SDK components](../sdk/#sdk-components) sections demonstrate simple
programmatic configuration API for key user-facing areas of the SDK. Consult the
code for complete API reference.