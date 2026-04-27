---
title: "Openaichatagent Support More Messages"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

By default, @AutoGen.OpenAI.OpenAIChatAgent only supports the @AutoGen.Core.IMessage<T> type where `T` is original request or response message from `Azure.AI.OpenAI`. To support more AutoGen built-in message types like @AutoGen.Core.TextMessage, @AutoGen.Core.ImageMessage, @AutoGen.Core.MultiModalMessage and so on, you can register the agent with @AutoGen.OpenAI.OpenAIChatRequestMessageConnector. The @AutoGen.OpenAI.OpenAIChatRequestMessageConnector will convert the message from AutoGen built-in message types to `Azure.AI.OpenAI.ChatRequestMessage` and vice versa.

import the required namespaces:
[!code-csharp[](../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/OpenAICodeSnippet.cs?name=using_statement)]

[!code-csharp[](../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/OpenAICodeSnippet.cs?name=register_openai_chat_message_connector)]
