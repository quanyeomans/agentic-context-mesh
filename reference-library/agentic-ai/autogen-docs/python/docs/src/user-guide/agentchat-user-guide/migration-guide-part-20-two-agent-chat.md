## Two-Agent Chat

In `v0.2`, you can create a two-agent chat for code execution as follows:

```python
from autogen.coding import LocalCommandLineCodeExecutor
from autogen.agentchat import AssistantAgent, UserProxyAgent

llm_config = {
    "config_list": [{"model": "gpt-4o", "api_key": "sk-xxx"}],
    "seed": 42,
    "temperature": 0,
}

assistant = AssistantAgent(
    name="assistant",
    system_message="You are a helpful assistant. Write all code in python. Reply only 'TERMINATE' if the task is done.",
    llm_config=llm_config,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
)

user_proxy = UserProxyAgent(
    name="user_proxy",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10,
    code_execution_config={"code_executor": LocalCommandLineCodeExecutor(work_dir="coding")},
    llm_config=False,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
)

chat_result = user_proxy.initiate_chat(assistant, message="Write a python script to print 'Hello, world!'")