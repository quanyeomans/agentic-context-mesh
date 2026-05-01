---
title: "Blueprints and reference implementations"
source: OpenTelemetry Documentation
source_url: https://github.com/open-telemetry/opentelemetry.io
licence: CC-BY-4.0
domain: engineering
subdomain: opentelemetry-docs
date_added: 2026-04-25
---

Adopting OpenTelemetry at scale is not just a matter of configuring individual
components. It requires coordinated decisions across teams and systems. The
official project documentation explains how specific pieces of OpenTelemetry
work, but many adopters need help connecting those pieces into a cohesive,
production-ready architecture.

This section provides high-level guidance and architectural patterns for
designing and operating OpenTelemetry in real-world environments. It focuses on
the challenges organizations face and maps these challenges to proven approaches
and best practices you can apply in your own environment.

There is no single “correct” way to deploy OpenTelemetry, so this guidance aims
to address all organizational structures, not to force a specific one. With this
flexibility in mind, you can find two types of reference documents in this
section:

- Blueprints are living documents that solve common adoption and implementation
  challenges in a given environment. Each blueprint is tightly scoped to address
  specific challenges, so you might need to refer to multiple blueprints,
  depending on your environment.
- Reference implementations are snapshots in time that show how real-world
  organizations use OpenTelemetry to build scalable, resilient pipelines that
  send application telemetry to observability backends.
