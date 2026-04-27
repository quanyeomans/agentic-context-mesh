---
title: "AutoGen"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

<style>
.hero-title {
  font-size: 60px;
  font-weight: bold;
  margin: 2rem auto 0;
}

.wip-card {
  border: 1px solid var(--pst-color-success);
  background-color: var(--pst-color-success-bg);
  border-radius: .25rem;
  padding: 0.3rem;
  display: flex;
  justify-content: center;
  align-items: center;
  margin-bottom: 1rem;
}
</style>

# AutoGen


<h1 class="hero-title">
AutoGen
</h1>
<h3>
A framework for building AI agents and applications
</h3>


::::{grid}
:gutter: 2

:::{grid-item-card} {fas}`palette;pst-color-primary` Studio 
:shadow: none
:margin: 2 0 0 0
:columns: 12 12 12 12

An web-based UI for prototyping with agents without writing code.
Built on AgentChat.

```bash
pip install -U autogenstudio
autogenstudio ui --port 8080 --appdir ./myapp
```

_Start here if you are new to AutoGen and want to prototype with agents without writing code._

+++

```{button-ref} user-guide/autogenstudio-user-guide/index
:color: secondary

Get Started
```

:::

:::{grid-item-card}
:shadow: none
:margin: 2 0 0 0
:columns: 12 12 12 12


{fas}`people-group;pst-color-primary` AgentChat


A programming framework for building conversational single and multi-agent applications.
Built on Core. Requires Python 3.10+.

```python
# pip install -U "autogen-agentchat" "autogen-ext[openai]"
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main() -> None:
    agent = AssistantAgent("assistant", OpenAIChatCompletionClient(model="gpt-4o"))
    print(await agent.run(task="Say 'Hello World!'"))

asyncio.run(main())
```

_Start here if you are prototyping with agents using Python. [Migrating from AutoGen 0.2?](./user-guide/agentchat-user-guide/migration-guide.md)._

+++

```{button-ref} user-guide/agentchat-user-guide/quickstart
:color: secondary

Get Started
```

:::

:::{grid-item-card} {fas}`cube;pst-color-primary` Core 
:shadow: none
:margin: 2 0 0 0
:columns: 12 12 12 12

An event-driven programming framework for building scalable multi-agent AI systems. Example scenarios:

* Deterministic and dynamic agentic workflows for business processes.
* Research on multi-agent collaboration.
* Distributed agents for multi-language applications.

_Start here if you are getting serious about building multi-agent systems._

+++

```{button-ref} user-guide/core-user-guide/quickstart
:color: secondary

Get Started
```

:::

:::{grid-item-card} {fas}`puzzle-piece;pst-color-primary` Extensions 
:shadow: none
:margin: 2 0 0 0
:columns: 12 12 12 12

Implementations of Core and AgentChat components that interface with external services or other libraries.
You can find and use community extensions or create your own. Examples of built-in extensions:

* {py:class}`~autogen_ext.tools.mcp.McpWorkbench` for using Model-Context Protocol (MCP) servers.
* {py:class}`~autogen_ext.agents.openai.OpenAIAssistantAgent` for using Assistant API.
* {py:class}`~autogen_ext.code_executors.docker.DockerCommandLineCodeExecutor` for running model-generated code in a Docker container.
* {py:class}`~autogen_ext.runtimes.grpc.GrpcWorkerAgentRuntime` for distributed agents.

+++

[Discover Community Extensions](user-guide/extensions-user-guide/discover.html)
[Create New Extension](user-guide/extensions-user-guide/create-your-own.html)

:::

::::


```{toctree}
:maxdepth: 3
:hidden:

user-guide/agentchat-user-guide/index
user-guide/core-user-guide/index
user-guide/extensions-user-guide/index
Studio 
reference/index
```
