## Why These Examples Work with DACA
Each example leverages DACA’s core strengths:
- **Event-Driven Architecture (EDA)**: Enables real-time reactivity (e.g., flagging posts, responding to sensor data, updating recommendations).
- **Three-Tier Microservices**: Separates concerns (UI for users/doctors, agent logic, data storage), easing maintenance and scaling.
- **Stateless Computing**: Allows agents to scale horizontally (e.g., handling millions of users or devices).
- **Scheduled Computing (CronJobs)**: Supports proactive tasks (e.g., retraining models, optimizing rules).
- **Human-in-the-Loop (HITL)**: Ensures accountability in critical scenarios (e.g., medical diagnoses, door unlocks).
- **Progressive Scaling**: Ascends from local testing to planet-scale deployment, using free tiers to minimize early costs.

---

### Summary of the Dapr Agentic Cloud Ascent (DACA) architecture:

The Dapr Agentic Cloud Ascent (DACA) architecture aims to build a planet-scale, multi-AI agent system foundationally leveraging Kubernetes for orchestration and Dapr for distributed application capabilities. Core AI agents, developed using OpenAI or Google SDKs, are encapsulated as stateful Dapr Actors ("Agentic Actors"), simplifying state management and concurrency for individual agents. Similarly, user sessions are managed as stateful entities by Dapr Actors, interacting with the User Interface via FastAPI-based REST APIs, while internal communication between agents relies on Dapr's publish/subscribe mechanism for loose coupling and scalability.

For interactions beyond the organizational boundary, designated agents implement a specified A2A (Agent-to-Agent) protocol, enabling standardized external communication. Tool usage across agents is standardized via a defined MCP protocol. To address the challenge of long-running tasks that could block the single-threaded actors, the architecture incorporates Dapr Workflows; actors delegate these lengthy processes to the workflow engine, ensuring the actors remain responsive while the workflows manage the complex, potentially multi-step tasks durably and asynchronously.
___