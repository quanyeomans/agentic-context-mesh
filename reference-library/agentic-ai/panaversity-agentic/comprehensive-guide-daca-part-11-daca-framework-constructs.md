## DACA Framework Constructs
DACA’s minimalist stack balances simplicity and power, enabling any agentic workflow. Here are the core components:

1. **LLM APIs**:
   - **Choice**: OpenAI Chat Completion (industry standard), Responses API. Prototyping can use Google Gemini (free tier).
   - **Purpose**: Powers agent reasoning and task execution with robust, agent-friendly features (e.g., function calling).

2. **Lightweight Agents and MCP Servers**:

   - **Choice**: OpenAI Agents SDK for modular, task-specific agents with guardrails, tool integration, and handoffs. MCP Servers for standardized Tool calling.
   - **Purpose**: Minimizes resource use while enabling scalable, collaborative workflows.

<p align="center">

</p>

3. **REST APIs**:
   - **Choice**: FastAPI for high-performance, asynchronous communication.
   - **Purpose**: Facilitates stateless, real-time interactions between users, agents, and crews.

4. **Stateless Serverless Docker Containers**:
   - **Choice**: Docker for packaging agents, APIs, and MCP servers.
   - **Purpose**: Ensures portability, scalability, and efficient resource use across environments.

5. **Asynchronous Message Passing**:

   - **Choice**: RabbitMQ (prototyping, free tier via CloudAMQP), Kafka (production on Kubernetes).
   - **Purpose**: Decouples components and agents for resilience and event-driven workflows.

<p align="center">

</p>

6. **Scheduled Container Invocation**:
   - **Choice**:
     - Local: python-crontab (Linux/Mac), APScheduler (Windows), Schedule (in-process).
     - Prototyping: cron-job.org (free).
     - Production: Kubernetes CronJobs.
   - **Purpose**: Supports batch processing and periodic tasks (e.g., pulling messages, retraining models).

7. **Relational Managed Database Services**:
   - **Choice**: CockroachDB Serverless (free tier, distributed SQL), Postgres (production on Kubernetes).
   - **Purpose**: Stores agent states, user data, and logs with ACID compliance. SQLModel (ORM) abstracts provider switches.

8. **In-Memory Data Store**:
   - **Choice**: Upstash Redis (free tier, serverless).
   - **Purpose**: High-performance caching, session storage, and message brokering.

9. **A2A Protocol**: Standardizes agent-to-agent communication with Agent Cards and HTTP endpoints.

10. **Distributed Application Runtime (Dapr)**:

    - **Purpose**: Simplifies distributed systems with standardized building blocks (state, pub/sub, workflows).
    - **Implementation**: Runs as a sidecar container, managing state (Redis, CockroachDB), messaging (RabbitMQ, Kafka), and workflows.
    - **Optional**: Dapr Agents and Dapr Workflows for advanced orchestration.
    - **Why It Matters**: Ensures resilience, abstracts infra complexity, and future-proofs the system.


<p align="center">

</p>

---
### A2A in DACA: Enabling Agentia World
A2A, launched by Google with over 50 partners, is integral to DACA. It uses HTTP, SSE, and JSON-RPC to enable secure, modality-agnostic (text, audio, video) agent communication. Key A2A features in DACA include:

- **Agent Cards**: JSON files (/.well-known/agent.json) advertise capabilities, enabling discovery.
- **Task Management**: Agents initiate and process tasks with real-time feedback via A2A endpoints.
- **Interoperability**: Connects agents across platforms, supporting Agentia’s vision of a global network.
- **Security**: Enterprise-grade authentication ensures trust in cross-domain dialogues.

<p align="center">

</p>

A2A enables interaction between a "client" agent and a "remote" agent. The client agent creates and sends tasks, while the remote agent executes them to deliver accurate information or perform the appropriate action. This process relies on several essential features:

- **Capability discovery**: Agents can share their skills through a JSON-based “Agent Card,” enabling the client agent to select the most suitable remote agent for a task and connect via A2A.
- **Task management**: The client and remote agents focus on completing tasks to meet user needs. Tasks, defined by the protocol, follow a lifecycle—some are finished quickly, while others, especially prolonged ones, require ongoing communication to keep both agents aligned on progress. The result of a task is called an “artifact.”
- **Collaboration**: Agents exchange messages to share context, responses, artifacts, or user guidance.
- **User experience negotiation**: Messages contain “parts,” fully developed content pieces like generated images, each with a defined content type. This allows the agents to agree on the proper format and adapt to the user’s interface preferences, such as support for iframes, videos, web forms, and other elements.

---