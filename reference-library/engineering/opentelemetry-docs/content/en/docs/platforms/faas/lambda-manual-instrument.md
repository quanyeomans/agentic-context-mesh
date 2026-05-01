---
title: "Lambda Manual Instrumentation"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

For languages not covered in the Lambda auto-instrumentation document, the
community does not have a standalone instrumentation layer.

Users will need to follow the generic instrumentation guidance for their chosen
language and add the Collector Lambda layer to submit their data.

## Add the ARN of the OTel Collector Lambda layer

See the [Collector Lambda layer guidance](../lambda-collector/) to add the layer
to your application and configure the Collector. We recommend you add this
first.

## Instrument the Lambda with OTel

Review the [language instrumentation guidance](/docs/languages/) on how to
manually instrument your application.

## Publish your Lambda

Publish a new version of your Lambda to deploy the new changes and
instrumentation.
