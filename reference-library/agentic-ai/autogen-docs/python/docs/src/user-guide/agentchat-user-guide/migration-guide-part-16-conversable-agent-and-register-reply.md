## Conversable Agent and Register Reply

In `v0.2`, you can create a conversable agent and register a reply function as follows:

```python
from typing import Any, Dict, List, Optional, Tuple, Union
from autogen.agentchat import ConversableAgent

llm_config = {
    "config_list": [{"model": "gpt-4o", "api_key": "sk-xxx"}],
    "seed": 42,
    "temperature": 0,
}

conversable_agent = ConversableAgent(
    name="conversable_agent",
    system_message="You are a helpful assistant.",
    llm_config=llm_config,
    code_execution_config={"work_dir": "coding"},
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10,
)

def reply_func(
    recipient: ConversableAgent,
    messages: Optional[List[Dict]] = None,
    sender: Optional[Agent] = None,
    config: Optional[Any] = None,
) -> Tuple[bool, Union[str, Dict, None]]:
    # Custom reply logic here
    return True, "Custom reply"