---
title: "Guardrails Chainlit Demo"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# Guardrails Chainlit Demo

## Exemplar 
This agent will reject math related questions like: 

```bash
Hello, can you help me solve for x: 2x + 3 = 11?
```

## Setup

    uv add openai-agents python-dotenv chainlit

Give these commands to run the project:

    cd chatbot

    uv venv

On Mac:

    source .venv/bin/activate

Using uv to run the project

    uv run chainlit run main.py -w
