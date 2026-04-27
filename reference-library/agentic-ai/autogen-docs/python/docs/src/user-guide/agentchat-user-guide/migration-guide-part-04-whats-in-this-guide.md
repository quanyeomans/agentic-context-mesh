## What's in this guide?

We provide a detailed guide on how to migrate your existing codebase from `v0.2` to `v0.4`.

See each feature below for detailed information on how to migrate.

- [Migration Guide for v0.2 to v0.4](#migration-guide-for-v02-to-v04)
  - [What is `v0.4`?](#what-is-v04)
  - [New to AutoGen?](#new-to-autogen)
  - [What's in this guide?](#whats-in-this-guide)
  - [Model Client](#model-client)
    - [Use component config](#use-component-config)
    - [Use model client class directly](#use-model-client-class-directly)
  - [Model Client for OpenAI-Compatible APIs](#model-client-for-openai-compatible-apis)
  - [Model Client Cache](#model-client-cache)
  - [Assistant Agent](#assistant-agent)
  - [Multi-Modal Agent](#multi-modal-agent)
  - [User Proxy](#user-proxy)
  - [RAG Agent](#rag-agent)
  - [Conversable Agent and Register Reply](#conversable-agent-and-register-reply)
  - [Save and Load Agent State](#save-and-load-agent-state)
  - [Two-Agent Chat](#two-agent-chat)
  - [Tool Use](#tool-use)
  - [Chat Result](#chat-result)
  - [Conversion between v0.2 and v0.4 Messages](#conversion-between-v02-and-v04-messages)
  - [Group Chat](#group-chat)
  - [Group Chat with Resume](#group-chat-with-resume)
  - [Save and Load Group Chat State](#save-and-load-group-chat-state)
  - [Group Chat with Tool Use](#group-chat-with-tool-use)
  - [Group Chat with Custom Selector (Stateflow)](#group-chat-with-custom-selector-stateflow)
  - [Nested Chat](#nested-chat)
  - [Sequential Chat](#sequential-chat)
  - [GPTAssistantAgent](#gptassistantagent)
  - [Long Context Handling](#long-context-handling)
  - [Observability and Control](#observability-and-control)
  - [Code Executors](#code-executors)

The following features currently in `v0.2`
will be provided in the future releases of `v0.4.*` versions:

- Model Client Cost [#4835](https://github.com/microsoft/autogen/issues/4835)
- Teachable Agent
- RAG Agent

We will update this guide when the missing features become available.