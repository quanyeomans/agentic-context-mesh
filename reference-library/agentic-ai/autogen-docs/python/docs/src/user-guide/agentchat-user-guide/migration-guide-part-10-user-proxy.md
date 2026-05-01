## User Proxy

In `v0.2`, you create a user proxy as follows:

```python
from autogen.agentchat import UserProxyAgent

user_proxy = UserProxyAgent(
    name="user_proxy",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10,
    code_execution_config=False,
    llm_config=False,
)
```

This user proxy would take input from the user through console, and would terminate
if the incoming message ends with "TERMINATE".

In `v0.4`, a user proxy is simply an agent that takes user input only, there is no
other special configuration needed. You can create a user proxy as follows:

```python
from autogen_agentchat.agents import UserProxyAgent

user_proxy = UserProxyAgent("user_proxy")
```

See {py:class}`~autogen_agentchat.agents.UserProxyAgent`
for more details and how to customize the input function with timeout.