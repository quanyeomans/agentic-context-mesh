---
title: "Semantickernelagent Simple Chat"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

You can chat with @AutoGen.SemanticKernel.SemanticKernelAgent using both streaming and non-streaming methods and use native `ChatMessageContent` type via `IMessage<ChatMessageContent>`.

The following example shows how to create an @AutoGen.SemanticKernel.SemanticKernelAgent and chat with it using non-streaming method:

[!code-csharp[](../../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/SemanticKernelCodeSnippet.cs?name=create_semantic_kernel_agent)]

@AutoGen.SemanticKernel.SemanticKernelAgent also supports streaming chat via @AutoGen.Core.IStreamingAgent.GenerateStreamingReplyAsync*.

[!code-csharp[](../../../samples/AgentChat/Autogen.Basic.Sample/CodeSnippet/SemanticKernelCodeSnippet.cs?name=create_semantic_kernel_agent_streaming)]
