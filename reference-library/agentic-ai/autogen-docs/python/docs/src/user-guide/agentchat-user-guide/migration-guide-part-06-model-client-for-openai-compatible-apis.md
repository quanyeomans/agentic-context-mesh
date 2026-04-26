## Model Client for OpenAI-Compatible APIs

You can use a the `OpenAIChatCompletionClient` to connect to an OpenAI-Compatible API,
but you need to specify the `base_url` and `model_info`.

```python
from autogen_ext.models.openai import OpenAIChatCompletionClient

custom_model_client = OpenAIChatCompletionClient(
    model="custom-model-name",
    base_url="https://custom-model.com/reset/of/the/path",
    api_key="placeholder",
    model_info={
        "vision": True,
        "function_calling": True,
        "json_output": True,
        "family": "unknown",
        "structured_output": True,
    },
)
```

> **Note**: We don't test all the OpenAI-Compatible APIs, and many of them
> works differently from the OpenAI API even though they may claim to suppor it.
> Please test them before using them.

Read about [Model Clients](./tutorial/models.ipynb)
in AgentChat Tutorial and more detailed information on [Core API Docs](../core-user-guide/components/model-clients.ipynb)

Support for other hosted models will be added in the future.