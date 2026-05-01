---
title: "Installation"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

# Installation

First-part maintained extensions are available in the `autogen-ext` package.

```sh
pip install "autogen-ext"
```

Extras:

- `langchain` needed for {py:class}`~autogen_ext.tools.langchain.LangChainToolAdapter`
- `azure` needed for {py:class}`~autogen_ext.code_executors.azure.ACADynamicSessionsCodeExecutor`
- `docker` needed for {py:class}`~autogen_ext.code_executors.docker.DockerCommandLineCodeExecutor`
- `openai` needed for {py:class}`~autogen_ext.models.openai.OpenAIChatCompletionClient`
