---
title: "Install the Collector"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

You can deploy the OpenTelemetry Collector on a variety of operating systems and
architectures. The following instructions show you how to download and install
the latest stable version of the Collector for your environment.

Before you begin, make sure you understand Collector fundamentals, including
[deployment patterns][], [components][], and [configuration][].

## Build from source

You can build the latest version of the Collector based on the local operating
system using the following commands:

```sh
git clone https://github.com/open-telemetry/opentelemetry-collector.git
cd opentelemetry-collector
make install-tools
make otelcorecol
```

[deployment patterns]: /docs/collector/deploy/
[components]: /docs/collector/components/
[configuration]: /docs/collector/configuration/
