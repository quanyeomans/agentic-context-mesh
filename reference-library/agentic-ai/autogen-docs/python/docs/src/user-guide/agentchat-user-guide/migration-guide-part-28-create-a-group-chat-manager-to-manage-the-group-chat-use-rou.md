# Create a group chat manager to manage the group chat, use round-robin selection method.
manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config, speaker_selection_method="round_robin")