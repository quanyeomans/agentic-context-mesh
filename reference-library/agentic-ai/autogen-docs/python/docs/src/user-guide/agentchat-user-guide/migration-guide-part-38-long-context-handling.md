## Long Context Handling

In `v0.2`, long context that overflows the model's context window can be handled
by using the `transforms` capability that is added to an `ConversableAgent`
after which is contructed.

The feedbacks from our community has led us to believe this feature is essential
and should be a built-in component of {py:class}`~autogen_agentchat.agents.AssistantAgent`, and can be used for
every custom agent.

In `v0.4`, we introduce the {py:class}`~autogen_core.model_context.ChatCompletionContext` base class that manages
message history and provides a virtual view of the history. Applications can use
built-in implementations such as {py:class}`~autogen_core.model_context.BufferedChatCompletionContext` to
limit the message history sent to the model, or provide their own implementations
that creates different virtual views.

To use {py:class}`~autogen_core.model_context.BufferedChatCompletionContext` in an {py:class}`~autogen_agentchat.agents.AssistantAgent` in a chatbot scenario.

```python
import asyncio
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.agents import AssistantAgent
from autogen_core import CancellationToken
from autogen_core.model_context import BufferedChatCompletionContext
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main() -> None:
    model_client = OpenAIChatCompletionClient(model="gpt-4o", seed=42, temperature=0)

    assistant = AssistantAgent(
        name="assistant",
        system_message="You are a helpful assistant.",
        model_client=model_client,
        model_context=BufferedChatCompletionContext(buffer_size=10), # Model can only view the last 10 messages.
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

In this example, the chatbot can only read the last 10 messages in the history.