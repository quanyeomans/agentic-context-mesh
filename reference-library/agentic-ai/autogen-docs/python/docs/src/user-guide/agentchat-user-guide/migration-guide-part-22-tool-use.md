## Tool Use

In `v0.2`, to create a tool use chatbot, you must have two agents, one for calling the tool and one for executing the tool.
You need to initiate a two-agent chat for every user request.

```python
from autogen.agentchat import AssistantAgent, UserProxyAgent, register_function

llm_config = {
    "config_list": [{"model": "gpt-4o", "api_key": "sk-xxx"}],
    "seed": 42,
    "temperature": 0,
}

tool_caller = AssistantAgent(
    name="tool_caller",
    system_message="You are a helpful assistant. You can call tools to help user.",
    llm_config=llm_config,
    max_consecutive_auto_reply=1, # Set to 1 so that we return to the application after each assistant reply as we are building a chatbot.
)

tool_executor = UserProxyAgent(
    name="tool_executor",
    human_input_mode="NEVER",
    code_execution_config=False,
    llm_config=False,
)

def get_weather(city: str) -> str:
    return f"The weather in {city} is 72 degree and sunny."