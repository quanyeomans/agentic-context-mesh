---
title: "Native Lib Alert"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

---
---

{{ if $noIntegrations }}

> [!IMPORTANT] Help wanted
>
> As of today, we don't know about any {{ $name }} library that has
> OpenTelemetry natively integrated. If you are aware of such a library, [let us
> know][new-issue].

{{ end }}

{{ if not $noIntegrations }}

> [!IMPORTANT] Help wanted
>
> If you are aware of a {{ $name }} library that has OpenTelemetry natively
> integrated, [let us know][new-issue].

{{ end }}

[new-issue]:
  https://github.com/open-telemetry/opentelemetry.io/issues/new/choose
