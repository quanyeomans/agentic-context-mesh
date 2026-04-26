## Group Chat with Tool Use

In `v0.2` group chat, when tools are involved, you need to register the tool functions on a user proxy,
and include the user proxy in the group chat. The tool calls made by other agents
will be routed to the user proxy to execute.

We have observed numerous issues with this approach, such as the the tool call
routing not working as expected, and the tool call request and result cannot be
accepted by models without support for function calling.

In `v0.4`, there is no need to register the tool functions on a user proxy,
as the tools are directly executed within the {py:class}`~autogen_agentchat.agents.AssistantAgent`,
which publishes the response from the tool to the group chat.
So the group chat manager does not need to be involved in routing tool calls.

See [Selector Group Chat Tutorial](./selector-group-chat.ipynb) for an example
of using tools in a group chat.