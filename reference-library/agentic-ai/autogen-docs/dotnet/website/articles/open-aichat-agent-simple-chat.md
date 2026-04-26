---
title: "Openaichatagent Simple Chat"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

The following example shows how to create an @AutoGen.OpenAI.OpenAIChatAgent and chat with it.

Firsly, import the required namespaces:
[!code-csharp[](../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/OpenAICodeSnippet.cs?name=using_statement)]

Then, create an @AutoGen.OpenAI.OpenAIChatAgent and chat with it:
[!code-csharp[](../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/OpenAICodeSnippet.cs?name=create_openai_chat_agent)]

@AutoGen.OpenAI.OpenAIChatAgent also supports streaming chat via @AutoGen.Core.IAgent.GenerateStreamingReplyAsync*.

[!code-csharp[](../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/OpenAICodeSnippet.cs?name=create_openai_chat_agent_streaming)]
