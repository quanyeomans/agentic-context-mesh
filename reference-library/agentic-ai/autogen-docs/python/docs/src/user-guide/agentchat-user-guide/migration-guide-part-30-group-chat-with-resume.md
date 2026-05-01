## Group Chat with Resume

In `v0.2`, group chat with resume is a bit complicated. You need to explicitly
save the group chat messages and load them back when you want to resume the chat.
See [Resuming Group Chat in v0.2](https://microsoft.github.io/autogen/0.2/docs/topics/groupchat/resuming_groupchat) for more details.

In `v0.4`, you can simply call `run` or `run_stream` again with the same group chat object to resume the chat. To export and load the state, you can use
`save_state` and `load_state` methods.

```python
import asyncio
import json
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient

def create_team(model_client : OpenAIChatCompletionClient) -> RoundRobinGroupChat:
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
    group_chat = RoundRobinGroupChat([writer, critic], termination_condition=termination)

    return group_chat


async def main() -> None:
    model_client = OpenAIChatCompletionClient(model="gpt-4o", seed=42, temperature=0)
    # Create team.
    group_chat = create_team(model_client)

    # `run_stream` returns an async generator to stream the intermediate messages.
    stream = group_chat.run_stream(task="Write a short story about a robot that discovers it has feelings.")
    # `Console` is a simple UI to display the stream.
    await Console(stream)

    # Save the state of the group chat and all participants.
    state = await group_chat.save_state()
    with open("group_chat_state.json", "w") as f:
        json.dump(state, f)

    # Create a new team with the same participants configuration.
    group_chat = create_team(model_client)

    # Load the state of the group chat and all participants.
    with open("group_chat_state.json", "r") as f:
        state = json.load(f)
    await group_chat.load_state(state)

    # Resume the chat.
    stream = group_chat.run_stream(task="Translate the story into Chinese.")
    await Console(stream)

    # Close the connection to the model client.
    await model_client.close()

asyncio.run(main())
```