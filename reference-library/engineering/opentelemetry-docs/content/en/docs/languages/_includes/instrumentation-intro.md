---
title: "Instrumentation Intro"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

---
---

[Instrumentation](/docs/concepts/instrumentation/) is the act of adding
observability code to an app yourself.

If you're instrumenting an app, you need to use the OpenTelemetry SDK for your
language. You'll then use the SDK to initialize OpenTelemetry and the API to
instrument your code. This will emit telemetry from your app, and any library
you installed that also comes with instrumentation.

If you're instrumenting a library, only install the OpenTelemetry API package
for your language. Your library will not emit telemetry on its own. It will only
emit telemetry when it is part of an app that uses the OpenTelemetry SDK. For
more on instrumenting libraries, see
[Libraries](/docs/concepts/instrumentation/libraries/).

For more information about the OpenTelemetry API and SDK, see the
[specification](/docs/specs/otel/).
