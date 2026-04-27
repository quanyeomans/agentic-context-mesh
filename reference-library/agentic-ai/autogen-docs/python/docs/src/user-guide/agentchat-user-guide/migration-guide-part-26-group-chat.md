## Group Chat

In `v0.2`, you need to create a `GroupChat` class and pass it into a
`GroupChatManager`, and have a participant that is a user proxy to initiate the chat.
For a simple scenario of a writer and a critic, you can do the following:

```python
from autogen.agentchat import AssistantAgent, GroupChat, GroupChatManager

llm_config = {
    "config_list": [{"model": "gpt-4o", "api_key": "sk-xxx"}],
    "seed": 42,
    "temperature": 0,
}

writer = AssistantAgent(
    name="writer",
    description="A writer.",
    system_message="You are a writer.",
    llm_config=llm_config,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("APPROVE"),
)

critic = AssistantAgent(
    name="critic",
    description="A critic.",
    system_message="You are a critic, provide feedback on the writing. Reply only 'APPROVE' if the task is done.",
    llm_config=llm_config,
)