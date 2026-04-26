## RAG Agent

In `v0.2`, there was the concept of teachable agents as well as a RAG agents that could take a database config.

```python
teachable_agent = ConversableAgent(
    name="teachable_agent",
    llm_config=llm_config
)