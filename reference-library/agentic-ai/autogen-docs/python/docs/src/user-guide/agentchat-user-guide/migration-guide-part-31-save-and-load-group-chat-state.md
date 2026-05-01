## Save and Load Group Chat State

In `v0.2`, you need to explicitly save the group chat messages and load them back when you want to resume the chat.

In `v0.4`, you can simply call `save_state` and `load_state` methods on the group chat object.
See [Group Chat with Resume](#group-chat-with-resume) for an example.