# Register the tool function to the tool caller and executor.
register_function(get_weather, caller=tool_caller, executor=tool_executor)

while True:
    user_input = input("User: ")
    if user_input == "exit":
        break
    chat_result = tool_executor.initiate_chat(
        tool_caller,
        message=user_input,
        summary_method="reflection_with_llm", # To let the model reflect on the tool use, set to "last_msg" to return the tool call result directly.
    )
    print("Assistant:", chat_result.summary)
```

In `v0.4`, you really just need one agent -- the {py:class}`~autogen_agentchat.agents.AssistantAgent` -- to handle
both the tool calling and tool execution.

```python
import asyncio
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage

def get_weather(city: str) -> str: # Async tool is possible too.
    return f"The weather in {city} is 72 degree and sunny."

async def main() -> None:
    model_client = OpenAIChatCompletionClient(model="gpt-4o", seed=42, temperature=0)
    assistant = AssistantAgent(
        name="assistant",
        system_message="You are a helpful assistant. You can call tools to help user.",
        model_client=model_client,
        tools=[get_weather],
        reflect_on_tool_use=True, # Set to True to have the model reflect on the tool use, set to False to return the tool call result directly.
    )
    while True:
        user_input = input("User: ")
        if user_input == "exit":
            break
        response = await assistant.on_messages([TextMessage(content=user_input, source="user")], CancellationToken())
        print("Assistant:", response.chat_message.to_text())
    await model_client.close()

asyncio.run(main())
```

When using tool-equipped agents inside a group chat such as
{py:class}`~autogen_agentchat.teams.RoundRobinGroupChat`,
you simply do the same as above to add tools to the agents, and create a
group chat with the agents.