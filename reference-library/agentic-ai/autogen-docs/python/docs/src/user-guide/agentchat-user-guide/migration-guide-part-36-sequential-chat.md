## Sequential Chat

In `v0.2`, sequential chat is supported by using the `initiate_chats` function.
It takes input a list of dictionary configurations for each step of the sequence.
See [Sequential Chat in v0.2](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns#sequential-chats)
for more details.

Base on the feedback from the community, the `initiate_chats` function
is too opinionated and not flexible enough to support the diverse set of scenarios that
users want to implement. We often find users struggling to get the `initiate_chats` function
to work when they can easily glue the steps together usign basic Python code.
Therefore, in `v0.4`, we do not provide a built-in function for sequential chat in the AgentChat API.

Instead, you can create an event-driven sequential workflow using the Core API,
and use the other components provided the AgentChat API to implement each step of the workflow.
See an example of sequential workflow in the [Core API Tutorial](../core-user-guide/design-patterns/sequential-workflow.ipynb).

We recognize that the concept of workflow is at the heart of many applications,
and we will provide more built-in support for workflows in the future.