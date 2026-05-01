## Observability and Control

In `v0.4` AgentChat, you can observe the agents by using the `on_messages_stream` method
which returns an async generator to stream the inner thoughts and actions of the agent.
For teams, you can use the `run_stream` method to stream the inner conversation among the agents in the team.
Your application can use these streams to observe the agents and teams in real-time.

Both the `on_messages_stream` and `run_stream` methods takes a {py:class}`~autogen_core.CancellationToken` as a parameter
which can be used to cancel the output stream asynchronously and stop the agent or team.
For teams, you can also use termination conditions to stop the team when a certain condition is met.
See [Termination Condition Tutorial](./tutorial/termination.ipynb)
for more details.

Unlike the `v0.2` which comes with a special logging module, the `v0.4` API
simply uses Python's `logging` module to log events such as model client calls.
See [Logging](../core-user-guide/framework/logging.md)
in the Core API documentation for more details.