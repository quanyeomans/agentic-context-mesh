## Samplers

Samplers let you control potential noise and overhead introduced by
OpenTelemetry instrumentation by selecting which traces you want to collect and
export. See
[the OpenTelemetry specification](/docs/specs/otel/configuration/sdk-environment-variables/#general-sdk-configuration)
for more details.

| Environment variable      | Description                                           | Default value           | Status                                              |
| ------------------------- | ----------------------------------------------------- | ----------------------- | --------------------------------------------------- |
| `OTEL_TRACES_SAMPLER`     | Sampler to be used for traces \[1\]                   | `parentbased_always_on` | [Stable](/docs/specs/otel/versioning-and-stability) |
| `OTEL_TRACES_SAMPLER_ARG` | String value to be used as the sampler argument \[2\] |                         | [Stable](/docs/specs/otel/versioning-and-stability) |

\[1\]: Supported values are:

- `always_on`,
- `always_off`,
- `traceidratio`,
- `parentbased_always_on`,
- `parentbased_always_off`,
- `parentbased_traceidratio`.

\[2\]: For `traceidratio` and `parentbased_traceidratio` samplers: Sampling
probability, a number in the [0..1] range, e.g. "0.25". Default is 1.0.