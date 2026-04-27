# Instantiate a Teachability object. Its parameters are all optional.
teachability = Teachability(
    reset_db=False,
    path_to_db_dir="./tmp/interactive/teachability_db"
)

teachability.add_to_agent(teachable_agent)
```

In `v0.4`, you can implement a RAG agent using the {py:class}`~autogen_core.memory.Memory` class. Specifically, you can define a memory store class, and pass that as a parameter to the assistant agent. See the [Memory](memory.ipynb) tutorial for more details.

This clear separation of concerns allows you to implement a memory store that uses any database or storage system you want (you have to inherit from the `Memory` class) and use it with an assistant agent. The example below shows how to use a ChromaDB vector memory store with the assistant agent. In addition, your application logic should determine how and when to add content to the memory store. For example, you may choose to call `memory.add` for every response from the assistant agent or use a separate LLM call to determine if the content should be added to the memory store.

```python