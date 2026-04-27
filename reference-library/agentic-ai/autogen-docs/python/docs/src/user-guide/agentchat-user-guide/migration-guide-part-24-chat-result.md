## Chat Result

In `v0.2`, you get a `ChatResult` object from the `initiate_chat` method.
For example:

```python
chat_result = tool_executor.initiate_chat(
    tool_caller,
    message=user_input,
    summary_method="reflection_with_llm",
)
print(chat_result.summary) # Get LLM-reflected summary of the chat.
print(chat_result.chat_history) # Get the chat history.
print(chat_result.cost) # Get the cost of the chat.
print(chat_result.human_input) # Get the human input solicited by the chat.
```

See [ChatResult Docs](https://microsoft.github.io/autogen/0.2/docs/reference/agentchat/chat#chatresult)
for more details.

In `v0.4`, you get a {py:class}`~autogen_agentchat.base.TaskResult` object from a `run` or `run_stream` method.
The {py:class}`~autogen_agentchat.base.TaskResult` object contains the `messages` which is the message history
of the chat, including both agents' private (tool calls, etc.) and public messages.

There are some notable differences between {py:class}`~autogen_agentchat.base.TaskResult` and `ChatResult`:

- The `messages` list in {py:class}`~autogen_agentchat.base.TaskResult` uses different message format than the `ChatResult.chat_history` list.
- There is no `summary` field. It is up to the application to decide how to summarize the chat using the `messages` list.
- `human_input` is not provided in the {py:class}`~autogen_agentchat.base.TaskResult` object, as the user input can be extracted from the `messages` list by filtering with the `source` field.
- `cost` is not provided in the {py:class}`~autogen_agentchat.base.TaskResult` object, however, you can calculate the cost based on token usage. It would be a great community extension to add cost calculation. See [community extensions](../extensions-user-guide/discover.md).