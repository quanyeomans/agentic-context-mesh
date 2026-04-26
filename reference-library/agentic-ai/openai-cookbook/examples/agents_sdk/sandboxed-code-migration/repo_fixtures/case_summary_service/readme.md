---
title: "Case summary service"
source: OpenAI Cookbook
source_url: https://github.com/openai/openai-cookbook
licence: MIT
domain: agentic-ai
subdomain: openai-cookbook
date_added: 2026-04-25
---

# Case summary service

Small offline fixture for the sandboxed migration cookbook.

The pre-migration service wraps a Chat Completions call and uses it to summarize
internal case notes. Tests use fakes; they should never call the network.
