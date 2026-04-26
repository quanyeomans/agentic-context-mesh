# Initiate the chat with the editor, intermediate messages are printed to the console directly.
result = editor.initiate_chat(
    manager,
    message="Write a short story about a robot that discovers it has feelings.",
)
print(result.summary)
```

In `v0.4`, you can use the {py:class}`~autogen_agentchat.teams.RoundRobinGroupChat` to achieve the same behavior.

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main() -> None:
    model_client = OpenAIChatCompletionClient(model="gpt-4o", seed=42, temperature=0)

    writer = AssistantAgent(
        name="writer",
        description="A writer.",
        system_message="You are a writer.",
        model_client=model_client,
    )

    critic = AssistantAgent(
        name="critic",
        description="A critic.",
        system_message="You are a critic, provide feedback on the writing. Reply only 'APPROVE' if the task is done.",
        model_client=model_client,
    )

    # The termination condition is a text termination, which will cause the chat to terminate when the text "APPROVE" is received.
    termination = TextMentionTermination("APPROVE")

    # The group chat will alternate between the writer and the critic.
    group_chat = RoundRobinGroupChat([writer, critic], termination_condition=termination, max_turns=12)

    # `run_stream` returns an async generator to stream the intermediate messages.
    stream = group_chat.run_stream(task="Write a short story about a robot that discovers it has feelings.")
    # `Console` is a simple UI to display the stream.
    await Console(stream)
    # Close the connection to the model client.
    await model_client.close()

asyncio.run(main())
```

For LLM-based speaker selection, you can use the {py:class}`~autogen_agentchat.teams.SelectorGroupChat` instead.
See [Selector Group Chat Tutorial](./selector-group-chat.ipynb)
and {py:class}`~autogen_agentchat.teams.SelectorGroupChat` for more details.

> **Note**: In `v0.4`, you do not need to register functions on a user proxy to use tools
> in a group chat. You can simply pass the tool functions to the {py:class}`~autogen_agentchat.agents.AssistantAgent` as shown in the [Tool Use](#tool-use) section.
> The agent will automatically call the tools when needed.
> If your tool doesn't output well formed response, you can use the `reflect_on_tool_use` parameter to have the model reflect on the tool use.