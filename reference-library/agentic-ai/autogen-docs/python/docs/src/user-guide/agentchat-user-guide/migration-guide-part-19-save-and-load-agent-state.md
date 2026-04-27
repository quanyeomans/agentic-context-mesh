## Save and Load Agent State

In `v0.2` there is no built-in way to save and load an agent's state: you need
to implement it yourself by exporting the `chat_messages` attribute of `ConversableAgent`
and importing it back through the `chat_messages` parameter.

In `v0.4`, you can call `save_state` and `load_state` methods on agents to save and load their state.

```python
import asyncio
import json
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

    # Save the state.
    state = await assistant.save_state()

    # (Optional) Write state to disk.
    with open("assistant_state.json", "w") as f:
        json.dump(state, f)

    # (Optional) Load it back from disk.
    with open("assistant_state.json", "r") as f:
        state = json.load(f)
        print(state) # Inspect the state, which contains the chat history.

    # Carry on the chat.
    response = await assistant.on_messages([TextMessage(content="Tell me a joke.", source="user")], cancellation_token)
    print(response)

    # Load the state, resulting the agent to revert to the previous state before the last message.
    await assistant.load_state(state)

    # Carry on the same chat again.
    response = await assistant.on_messages([TextMessage(content="Tell me a joke.", source="user")], cancellation_token)
    # Close the connection to the model client.
    await model_client.close()

asyncio.run(main())
```

You can also call `save_state` and `load_state` on any teams, such as {py:class}`~autogen_agentchat.teams.RoundRobinGroupChat`
to save and load the state of the entire team.