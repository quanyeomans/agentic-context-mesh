# Capability recommender

Design specs for the capability/skill recommender — a feature that, given a
free-text description of the task an agent is about to do, returns a ranked
list of the kairix tools, external skills, slash-commands, sub-agents, and
workflows best suited to it, each with a ready-to-call invocation.

The feature is built in two deliverables, each with its own spec and plan:

| Spec | Scope | Status |
|---|---|---|
| [`recommender-mvp-design.md`](recommender-mvp-design.md) | Unified capability corpus + hot-path recommender (`kairix recommend` / `recommend_capabilities`) | Design |
| `affordance-loop-design.md` | Cold-path affordance-improvement loop (query+results+outcome logging → offline LLM analysis) | Planned (spec follows MVP) |

See [`connector-ingestion-architecture.md`](../connector-ingestion-architecture.md)
for the connector framework the corpus feeder rides, and
[`cli-mcp-feature-parity.md`](../cli-mcp-feature-parity.md) for the one
use-case / two-adapter surfacing contract the recommender follows.
