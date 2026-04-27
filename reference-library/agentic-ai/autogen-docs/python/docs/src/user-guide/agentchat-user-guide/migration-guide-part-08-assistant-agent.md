## Assistant Agent

In `v0.2`, you create an assistant agent as follows:

```python
from autogen.agentchat import AssistantAgent

llm_config = {
    "config_list": [{"model": "gpt-4o", "api_key": "sk-xxx"}],
    "seed": 42,
    "temperature": 0,
}

assistant = AssistantAgent(
    name="assistant",
    system_message="You are a helpful assistant.",
    llm_config=llm_config,
)
```

In `v0.4`, it is similar, but you need to specify `model_client` instead of `llm_config`.

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

model_client = OpenAIChatCompletionClient(model="gpt-4o", api_key="sk-xxx", seed=42, temperature=0)

assistant = AssistantAgent(
    name="assistant",
    system_message="You are a helpful assistant.",
    model_client=model_client,
)
```

However, the usage is somewhat different. In `v0.4`, instead of calling `assistant.send`,
you call `assistant.on_messages` or `assistant.on_messages_stream` to handle incoming messages.
Furthermore, the `on_messages` and `on_messages_stream` methods are asynchronous,
and the latter returns an async generator to stream the inner thoughts of the agent.

Here is how you can call the assistant agent in `v0.4` directly, continuing from the above example:

```python
import asyncio
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.agents import AssistantAgent
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main() -> None:
    model_client = OpenAIChatCompletionClient(model="gpt-4o", seed=42, temperature=0)

    assistant = AssistantAgent(
        name="assistant",
        system_message="You are a helpful assistant.",
        model_client=model_client,
    )

    cancellation_token = CancellationToken()
    response = await assistant.on_messages([TextMessage(content="Hello!", source="user")], cancellation_token)
    print(response)

    await model_client.close()

asyncio.run(main())
```

The {py:class}`~autogen_core.CancellationToken` can be used to cancel the request asynchronously
when you call `cancellation_token.cancel()`, which will cause the `await`
on the `on_messages` call to raise a `CancelledError`.

Read more on [Agent Tutorial](./tutorial/agents.ipynb)
and {py:class}`~autogen_agentchat.agents.AssistantAgent`.