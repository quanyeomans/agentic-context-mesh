# NOTE: An async reply function will only be invoked with async send.
```

Rather than guessing what the `reply_func` does, all its parameters,
and what the `position` should be, in `v0.4`, we can simply create a custom agent
and implement the `on_messages`, `on_reset`, and `produced_message_types` methods.

```python
from typing import Sequence
from autogen_core import CancellationToken
from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.messages import TextMessage, BaseChatMessage
from autogen_agentchat.base import Response

class CustomAgent(BaseChatAgent):
    async def on_messages(self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken) -> Response:
        return Response(chat_message=TextMessage(content="Custom reply", source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        pass

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)
```

You can then use the custom agent in the same way as the {py:class}`~autogen_agentchat.agents.AssistantAgent`.
See [Custom Agent Tutorial](custom-agents.ipynb)
for more details.