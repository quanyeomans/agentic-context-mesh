---
title: "🤝 Handoffs"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# 🤝 Handoffs

**Handoff** = your current agent **transfers control** to another, more specialized agent to finish the task or the next stretch of the conversation. In the SDK, a handoff is exposed to the LLM **as a tool** (e.g., `transfer_to_refund_agent`). Use it when a specialist should *take over* and keep talking with the user.

---

## 💡 Why do we need handoffs?

When different parts of a problem are best handled by different specialists (billing, refunds, FAQs, research, etc.), handoffs let you route the conversation to the right agent at the right time. Think: **customer support** where a triage agent routes to “Order Status,” “Refunds,” or “FAQ” agents.

**Mental model (analogy):**

- *🛠 Agents-as-tools* = you keep the mic, briefly ask a colleague for a snippet.
- **🔄 Handoff** = you **transfer the call** to that colleague; they continue the conversation with the user.

Under the hood, the runner loop literally switches the “current agent” and continues from there.

---

## 🛠 Core SDK pieces you’ll use

- **📋Agent.handoffs** — list of agents (or `handoff(...)` objects) this agent can transfer to.
- **📋`handoff(...)`** — customize the handoff: override the tool name/description, add `on_handoff` callbacks, accept typed input with `input_type`, or edit history with `input_filter`.
- **📋Handoff is a tool** — the LLM sees a tool named like `transfer_to_<agent_name>`.

---

## 📜 Minimal example — triage that hands off

**Real-world story:** A “Triage Agent” decides whether to hand off to **Billing** or **Refunds**.

```python
from agents import Agent, Runner, handoff
import asyncio

billing_agent = Agent(name="Billing agent", instructions="Handle billing questions.")
refund_agent  = Agent(name="Refund agent",  instructions="Handle refunds.")

triage_agent = Agent(
    name="Triage agent",
    instructions=(
        "Help the user with their questions. "
        "If they ask about billing, handoff to the Billing agent. "
        "If they ask about refunds, handoff to the Refund agent."
    ),
    handoffs=[billing_agent, handoff(refund_agent)],  # either direct agent or `handoff(...)`
)

async def main():
    result = await Runner.run(triage_agent, "I need to check refund status.")
    print(result.final_output)

# asyncio.run(main())

```

## Quick debug trick

After a run, check:

- `result.final_output` → the specialist’s reply.
- `result.last_agent` → who actually answered (helpful for next turn continuity).
- `result.new_items` → look for `HandoffCallItem` then `HandoffOutputItem` (proof a handoff occurred).

---

## 🧪 Interactive Lab 1 — make your first handoff

**Goal:** See the routing happen.

1. 📝 Change the user input to a billing-style question and re-run (e.g., “My card was charged twice”).
2. 🔍 Print or inspect `result.new_items` to spot the **HandoffCallItem/HandoffOutputItem**—proof that a handoff happened.

> ✅ Checkpoint: You should see the final response come from the specialist agent and the “handoff” items present in new_items.
