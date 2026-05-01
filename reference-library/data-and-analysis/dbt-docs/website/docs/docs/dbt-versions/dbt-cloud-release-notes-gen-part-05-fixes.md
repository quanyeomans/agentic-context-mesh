## Fixes

### dbt Copilot and agents

- **Correct model routing for Azure OpenAI Responses API (BYOK customers)**: Azure OpenAI deployments now correctly pass the deployment name as the `model` field when using the Responses API, preventing misrouted requests when the deployment name differs from the model name.