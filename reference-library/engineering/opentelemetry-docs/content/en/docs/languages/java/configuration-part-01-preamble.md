---
title: "Configure the SDK"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

<?code-excerpt path-base="examples/java/configuration"?>

The [SDK](../sdk/) is the built-in reference implementation of the
[API](../api/), processing and exporting telemetry produced by instrumentation
API calls. Configuring the SDK to process and export appropriately is an
essential step to integrating OpenTelemetry into an application.

All SDK components have
[programmatic configuration APIs](#programmatic-configuration). This is the most
flexible, expressive way to configure the SDK. However, changing configuration
requires adjusting code and recompiling the application, and there is no
language interoperability since the API is written in java.

The [zero-code SDK autoconfigure](#zero-code-sdk-autoconfigure) module
configures SDK components through system properties or environment variables,
with various extension points for instances where the properties are
insufficient.

> [!NOTE] **Notes**
>
> - We recommend using the
>   [zero-code SDK autoconfigure](#zero-code-sdk-autoconfigure) module since it
>   reduces boilerplate code, allows reconfiguration without rewriting code or
>   recompiling the application, and has language interoperability.
> - The [Java agent](/docs/zero-code/java/agent/) and
>   [Spring starter](/docs/zero-code/java/spring-boot-starter/) automatically
>   configure the SDK using the zero-code SDK autoconfigure module, and install
>   instrumentation with it. All autoconfigure content is applicable to Java
>   agent and Spring starter users.