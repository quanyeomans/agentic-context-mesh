## What is `v0.4`?

Since the release of AutoGen in 2023, we have intensively listened to our community and users from small startups and large enterprises, gathering much feedback. Based on that feedback, we built AutoGen `v0.4`, a from-the-ground-up rewrite adopting an asynchronous, event-driven architecture to address issues such as observability, flexibility, interactive control, and scale.

The `v0.4` API is layered:
the [Core API](../core-user-guide/index.md) is the foundation layer offering a scalable, event-driven actor framework for creating agentic workflows;
the [AgentChat API](./index.md) is built on Core, offering a task-driven, high-level framework for building interactive agentic applications. It is a replacement for AutoGen `v0.2`.

Most of this guide focuses on `v0.4`'s AgentChat API; however, you can also build your own high-level framework using just the Core API.