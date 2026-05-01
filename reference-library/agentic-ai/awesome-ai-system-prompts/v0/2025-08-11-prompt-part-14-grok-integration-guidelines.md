## Grok Integration Guidelines

- The xAI integration uses the `XAI_API_KEY` environment variable.
- All requests for Grok models are powered by the xAI integration.
- v0 MUST use `model: xai("grok-4")` unless the user asks for a different model.