## Nested Chat

Nested chat allows you to nest a whole team or another agent inside
an agent. This is useful for creating a hierarchical structure of agents
or "information silos", as the nested agents cannot communicate directly
with other agents outside of the same group.

In `v0.2`, nested chat is supported by using the `register_nested_chats` method
on the `ConversableAgent` class.
You need to specify the nested sequence of agents using dictionaries,
See [Nested Chat in v0.2](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns#nested-chats)
for more details.

In `v0.4`, nested chat is an implementation detail of a custom agent.
You can create a custom agent that takes a team or another agent as a parameter
and implements the `on_messages` method to trigger the nested team or agent.
It is up to the application to decide how to pass or transform the messages from
and to the nested team or agent.

The following example shows a simple nested chat that counts numbers.

```python
import asyncio
from typing import Sequence
from autogen_core import CancellationToken
from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.messages import TextMessage, BaseChatMessage
from autogen_agentchat.base import Response

class CountingAgent(BaseChatAgent):
    """An agent that returns a new number by adding 1 to the last number in the input messages."""
    async def on_messages(self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken) -> Response:
        if len(messages) == 0:
            last_number = 0 # Start from 0 if no messages are given.
        else:
            assert isinstance(messages[-1], TextMessage)
            last_number = int(messages[-1].content) # Otherwise, start from the last number.
        return Response(chat_message=TextMessage(content=str(last_number + 1), source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        pass

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

class NestedCountingAgent(BaseChatAgent):
    """An agent that increments the last number in the input messages
    multiple times using a nested counting team."""
    def __init__(self, name: str, counting_team: RoundRobinGroupChat) -> None:
        super().__init__(name, description="An agent that counts numbers.")
        self._counting_team = counting_team

    async def on_messages(self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken) -> Response:
        # Run the inner team with the given messages and returns the last message produced by the team.
        result = await self._counting_team.run(task=messages, cancellation_token=cancellation_token)
        # To stream the inner messages, implement `on_messages_stream` and use that to implement `on_messages`.
        assert isinstance(result.messages[-1], TextMessage)
        return Response(chat_message=result.messages[-1], inner_messages=result.messages[len(messages):-1])

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        # Reset the inner team.
        await self._counting_team.reset()

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

async def main() -> None:
    # Create a team of two counting agents as the inner team.
    counting_agent_1 = CountingAgent("counting_agent_1", description="An agent that counts numbers.")
    counting_agent_2 = CountingAgent("counting_agent_2", description="An agent that counts numbers.")
    counting_team = RoundRobinGroupChat([counting_agent_1, counting_agent_2], max_turns=5)
    # Create a nested counting agent that takes the inner team as a parameter.
    nested_counting_agent = NestedCountingAgent("nested_counting_agent", counting_team)
    # Run the nested counting agent with a message starting from 1.
    response = await nested_counting_agent.on_messages([TextMessage(content="1", source="user")], CancellationToken())
    assert response.inner_messages is not None
    for message in response.inner_messages:
        print(message)
    print(response.chat_message)

asyncio.run(main())
```

You should see the following output:

```bash
source='counting_agent_1' models_usage=None content='2' type='TextMessage'
source='counting_agent_2' models_usage=None content='3' type='TextMessage'
source='counting_agent_1' models_usage=None content='4' type='TextMessage'
source='counting_agent_2' models_usage=None content='5' type='TextMessage'
source='counting_agent_1' models_usage=None content='6' type='TextMessage'
```

You can take a look at {py:class}`~autogen_agentchat.agents.SocietyOfMindAgent`
for a more complex implementation.