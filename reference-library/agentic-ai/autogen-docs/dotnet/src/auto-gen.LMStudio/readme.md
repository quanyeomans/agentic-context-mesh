---
title: "AutoGen.LMStudio"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

## AutoGen.LMStudio

This package provides support for consuming openai-like API from LMStudio local server.

## Installation
To use `AutoGen.LMStudio`, add the following package to your `.csproj` file:

```xml

    <PackageReference Include="AutoGen.LMStudio" Version="AUTOGEN_VERSION" />

```

## Usage
```csharp
using AutoGen.LMStudio;
var localServerEndpoint = "localhost";
var port = 5000;
var lmStudioConfig = new LMStudioConfig(localServerEndpoint, port);
var agent = new LMStudioAgent(
    name: "agent",
    systemMessage: "You are an agent that help user to do some tasks.",
    lmStudioConfig: lmStudioConfig)
    .RegisterPrintMessage(); // register a hook to print message nicely to console

await agent.SendAsync("Can you write a piece of C# code to calculate 100th of fibonacci?");
```

## Update history
### Update on 0.0.7 (2024-02-11)
- Add `LMStudioAgent` to support consuming openai-like API from LMStudio local server.
