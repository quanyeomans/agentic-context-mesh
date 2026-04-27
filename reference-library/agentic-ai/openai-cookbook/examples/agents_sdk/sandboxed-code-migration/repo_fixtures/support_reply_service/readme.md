---
title: "Customer Support Reply Bot"
source: OpenAI Cookbook
source_url: https://github.com/openai/openai-cookbook
licence: MIT
domain: agentic-ai
subdomain: openai-cookbook
date_added: 2026-04-25
---

# Customer Support Reply Bot

This tiny package drafts a support-agent reply with the OpenAI Python client.

The current implementation still uses Chat Completions through a small wrapper
in `customer_support_bot/client.py`. The migration target is in `MIGRATION.md`.
