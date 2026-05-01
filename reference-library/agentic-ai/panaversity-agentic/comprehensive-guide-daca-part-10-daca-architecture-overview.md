## DACA Architecture Overview

The DACA architecture is a layered, event-driven, stateless system that integrates human-in-the-loop (HITL) capabilities. It’s built on a **three-tier microservices architecture**, enhanced by Dapr, and supports both real-time and scheduled agentic workflows.

### Architecture Diagram Breakdown
<p align="center">

</p>

The provided architecture diagram illustrates the DACA framework:
- **Presentation Layer**: Next.js, Streamlit, or Chainlit for user interaction.
- **Business Logic Layer**:
  - **Containerized AI Agent**: OpenAI Agents SDK in stateless Docker containers, using FastAPI for RESTful interfaces and A2A for agent-to-agent communication.
  - **Containerized MCP Servers**: MCP Servers running in a stateless Docker containers being called by AI Agents via tool calling.
  - **Dapr Sidecar Container**: Handles state, messaging, and workflows.
  - **A2A Integration**: Agents expose capabilities via A2A Agent Cards and communicate via A2A endpoints.
- **Infrastructure Layer**:
  - **Deployment Platforms**: Kubernetes or Azure Container Apps (ACA) for scaling.
  - **Messaging**: Kafka, RabbitMQ, Redis for asynchronous events, with A2A for direct agent dialogues.
  - **Databases**: Postgres (Relational), Pinecone (Vector DB), Neo4j (Graph DB) for data persistence.

### Key Architectural Components
1. **Event-Driven Architecture (EDA)**:
   - **Purpose**: Drives real-time agent behavior through events (e.g., "UserInputReceived," "TaskCompleted").
   - **Implementation**: Producers (agents) emit events to an event bus (Kafka, RabbitMQ, Redis); consumers (other agents, HITL services) react asynchronously. A2A enhances EDA with direct, secure agent dialogues.
   - **Why It Fits**: Enables reactive, loosely coupled agent interactions—ideal for autonomy and scalability.

2. **Three-Tier Microservices Architecture**:
   - **Presentation Tier**: User interfaces (Next.js, Streamlit, Chainlit) for interacting with agents or HITL dashboards.
   - **Application Tier**: Stateless FastAPI services with OpenAI Agents SDK and A2A endpoints, supported by Dapr sidecars. It also includes stateless MCP Servers. 
   - **Data Tier**: Managed databases (CockroachDB, Upstash Redis) and specialized stores (Pinecone, Neo4j) for state and knowledge.

3. **Stateless Computing**:
   - **Purpose**: Containers (agents, APIs, MCP and A2A servers) are stateless, scaling horizontally without session data.
   - **Implementation**: State is offloaded to Dapr-managed stores (e.g., Redis, CockroachDB).
   - **Why It Fits**: Enhances scalability—any container instance can handle any request, simplifying load balancing.

4. **Scheduled Computing (CronJobs)**:
   - **Purpose**: Handles periodic tasks (e.g., model retraining, batch HITL reviews).
   - **Implementation**: Kubernetes CronJobs, cron-job.org (prototyping), or in-process schedulers (APScheduler, python-crontab).
   - **Why It Fits**: Supports proactive agent behaviors alongside reactive EDA.

5. **Human-in-the-Loop (HITL)**:
   - **Purpose**: Integrates human oversight for critical decisions, edge cases, or learning.
   - **Implementation**:
     - Agents emit HITL events (e.g., "HumanReviewRequired") when confidence is low.
     - Stateless HITL workers route tasks to a dashboard (presentation layer).
     - Humans approve/reject via UI, triggering "HumanDecisionMade" events to resume workflows.
     - CronJobs aggregate feedback for batch reviews or model updates.
   - **Why It Fits**: Ensures accountability while maintaining autonomy.

6. **Dapr Actors and Workflows**:
  - **Purpose**: Actors provide concurrent, stateful agent execution; workflows orchestrate complex, durable tasks.
  - **Implementation**: Actors manage agent state and communication; workflows define task sequences or parallel executions, leveraging Dapr’s state management and retry policies.
  - **Why It Fits**: Actors enable scalable agent interactions, while workflows ensure reliable coordination, critical for multi-agent systems.

  7. **Open Core and Managed Edges**:
  - **Purpose**: Combines the flexibility of open-source technologies with the efficiency of managed services to create a robust, scalable architecture.
  - **Implementation**: The core of the system—container orchestration (Kubernetes), distributed runtime (Dapr), and agent logic (OpenAI Agents SDK)—is built on open-source technologies, ensuring portability and community-driven enhancements. At the edges, managed services like CockroachDB Serverless (distributed SQL), Upstash Redis (in-memory store), and OpenAI APIs (AI inference) handle specialized tasks, reducing operational overhead and leveraging provider-optimized performance.
  - **Why It Fits**: This approach maximizes control over the system’s core while outsourcing complex, resource-intensive tasks to managed services, enabling rapid scaling and cost efficiency in agentic AI systems.

---