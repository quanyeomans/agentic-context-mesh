## Group Chat with Custom Selector (Stateflow)

In `v0.2` group chat, when the `speaker_selection_method` is set to a custom function,
it can override the default selection method. This is useful for implementing
a state-based selection method.
For more details, see [Custom Sepaker Selection in v0.2](https://microsoft.github.io/autogen/0.2/docs/topics/groupchat/customized_speaker_selection).

In `v0.4`, you can use the {py:class}`~autogen_agentchat.teams.SelectorGroupChat` with `selector_func` to achieve the same behavior.
The `selector_func` is a function that takes the current message thread of the group chat
and returns the next speaker's name. If `None` is returned, the LLM-based
selection method will be used.

Here is an example of using the state-based selection method to implement
a web search/analysis scenario.

```python
import asyncio
from typing import Sequence
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient