## Model Client

In `v0.2` you configure the model client as follows, and create the `OpenAIWrapper` object.

```python
from autogen.oai import OpenAIWrapper

config_list = [
    {"model": "gpt-4o", "api_key": "sk-xxx"},
    {"model": "gpt-4o-mini", "api_key": "sk-xxx"},
]

model_client = OpenAIWrapper(config_list=config_list)
```

> **Note**: In AutoGen 0.2, the OpenAI client would try configs in the list until one worked. 0.4 instead expects a specfic model configuration to be chosen.

In `v0.4`, we offer two ways to create a model client.

### Use component config

AutoGen 0.4 has a [generic component configuration system](../core-user-guide/framework/component-config.ipynb). Model clients are a great use case for this. See below for how to create an OpenAI chat completion client.

```python

from autogen_core.models import ChatCompletionClient

config = {
    "provider": "OpenAIChatCompletionClient",
    "config": {
        "model": "gpt-4o",
        "api_key": "sk-xxx" # os.environ["...']
    }
}

model_client = ChatCompletionClient.load_component(config)
```

### Use model client class directly

Open AI:

```python
from autogen_ext.models.openai import OpenAIChatCompletionClient

model_client = OpenAIChatCompletionClient(model="gpt-4o", api_key="sk-xxx")
```

Azure OpenAI:

```python
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

model_client = AzureOpenAIChatCompletionClient(
    azure_deployment="gpt-4o",
    azure_endpoint="https://<your-endpoint>.openai.azure.com/",
    model="gpt-4o",
    api_version="2024-09-01-preview",
    api_key="sk-xxx",
)
```

Read more on {py:class}`~autogen_ext.models.openai.OpenAIChatCompletionClient`.