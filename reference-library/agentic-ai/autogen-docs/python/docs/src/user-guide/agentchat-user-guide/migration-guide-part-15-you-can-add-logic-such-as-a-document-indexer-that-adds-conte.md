# you can add logic such as a document indexer that adds content to the memory store

assistant_agent = AssistantAgent(
    name="assistant_agent",
    model_client=OpenAIChatCompletionClient(
        model="gpt-4o",
    ),
    tools=[get_weather],
    memory=[chroma_user_memory],
)
```