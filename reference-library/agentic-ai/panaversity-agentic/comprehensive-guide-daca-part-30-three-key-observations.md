## Three key observations

### 1. Dapr is the glue that fulfils Factors 5, 6, 8, 9

By co-locating a Dapr sidecar with every agent, you inherit retries, state APIs, pub/sub, and workflow primitives. That collapses four factors into infrastructure rather than bespoke code .

### 2. A2A + MCP nail Factors 1 & 4 across organisational boundaries

Because both protocols push **JSON+HTTP** as the lingua franca, an agent inside Azure Container Apps can call a remote agent running on Hugging Face Spaces exactly the same way it calls its local MCP tool server ([github.com][2]).

### 3. “Open Core, Managed Edges” keeps Factor 12 honest

Kubernetes+Docker give you deterministic, stateless images; managed services take the heavy state. That separation makes it easy to prove your reducer is pure and swap edges without touching the core .

---