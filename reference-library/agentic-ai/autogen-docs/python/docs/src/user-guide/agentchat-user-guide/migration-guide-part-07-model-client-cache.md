## Model Client Cache

In `v0.2`, you can set the cache seed through the `cache_seed` parameter in the LLM config.
The cache is enabled by default.

```python
llm_config = {
    "config_list": [{"model": "gpt-4o", "api_key": "sk-xxx"}],
    "seed": 42,
    "temperature": 0,
    "cache_seed": 42,
}
```

In `v0.4`, the cache is not enabled by default, to use it you need to use a
{py:class}`~autogen_ext.models.cache.ChatCompletionCache` wrapper around the model client.

You can use a {py:class}`~autogen_ext.cache_store.diskcache.DiskCacheStore` or {py:class}`~autogen_ext.cache_store.redis.RedisStore` to store the cache.

```bash
pip install -U "autogen-ext[openai, diskcache, redis]"
```

Here's an example of using `diskcache` for local caching:

```python
import asyncio
import tempfile

from autogen_core.models import UserMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.models.cache import ChatCompletionCache, CHAT_CACHE_VALUE_TYPE
from autogen_ext.cache_store.diskcache import DiskCacheStore
from diskcache import Cache


async def main():
    with tempfile.TemporaryDirectory() as tmpdirname:
        # Initialize the original client
        openai_model_client = OpenAIChatCompletionClient(model="gpt-4o")

        # Then initialize the CacheStore, in this case with diskcache.Cache.
        # You can also use redis like:
        # from autogen_ext.cache_store.redis import RedisStore
        # import redis
        # redis_instance = redis.Redis()
        # cache_store = RedisCacheStore[CHAT_CACHE_VALUE_TYPE](redis_instance)
        cache_store = DiskCacheStore[CHAT_CACHE_VALUE_TYPE](Cache(tmpdirname))
        cache_client = ChatCompletionCache(openai_model_client, cache_store)

        response = await cache_client.create([UserMessage(content="Hello, how are you?", source="user")])
        print(response)  # Should print response from OpenAI
        response = await cache_client.create([UserMessage(content="Hello, how are you?", source="user")])
        print(response)  # Should print cached response
        await openai_model_client.close()


asyncio.run(main())
```