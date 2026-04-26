---
title: "Agent Lifecycle Hooks - Complete Beginner's Guide"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# Agent Lifecycle Hooks - Complete Beginner's Guide

## What are Agent Lifecycle Hooks? 🎣

Think of **Agent Lifecycle Hooks** like **event listeners** for a specific agent. Just like you might set up notifications for your phone (ring when someone calls, buzz when you get a text), you can set up "listeners" for your agent to know when important things happen.

### 🏠 House Analogy
Imagine your agent is like a **smart house** that can do different tasks. Agent lifecycle hooks are like **security cameras and sensors** placed specifically in that house to monitor what's happening:

- 📹 Camera at the front door (when someone enters)
- 🔧 Sensor in the workshop (when tools are used)  
- 🧠 Monitor in the study (when thinking/LLM calls happen)
- 🚪 Sensor at the exit (when leaving)

## AgentHooksBase Class

This is your "monitoring system" for a **single agent**. You attach this to `agent.hooks` to watch what happens with that specific agent.

### Available Hooks (Event Listeners)

#### 1. `on_start` - Agent Becomes Active 🕘
```python
async def on_start(context, agent):
    print(f"🕘 Agent {agent.name} is now in charge of handling the task")
```

**🏭 Factory Worker Analogy**: Like a worker clocking in and arriving at their workstation. They're now responsible for whatever work comes their way.

**When this happens:**
- The agent becomes the "active" agent in the system
- It's now responsible for handling the current conversation/task
- The agent might receive a handoff from another agent
- This happens **once** when the agent takes control

**Real Example:**
```
User asks: "What's the weather like?"
🕘 on_start: WeatherAgent becomes responsible for answering
```

#### 2. `on_end` - Agent Finishes Work ✅
```python
async def on_end(context, agent, output):
    print(f"✅ Agent {agent.name} completed work with result: {output}")
```

**🏭 Factory Worker Analogy**: Like a worker finishing their shift and clocking out. They've completed everything they needed to do.

**When this happens:**
- The agent has produced a final answer/result
- The agent's work is completely done
- The agent is about to become inactive
- This is the **last thing** that happens for this agent

**Real Example:**
```
✅ on_end: WeatherAgent completed with "It's 72°F and sunny!"
```

#### 3. `on_llm_start` - Agent Asks the AI Brain 📞
```python
async def on_llm_start(context, agent, system_prompt, input_items):
    print(f"📞 Agent {agent.name} is asking the AI for help with: {input_items}")
```

**🏭 Factory Worker Analogy**: Like a worker picking up the phone to call an expert consultant. They need advice on how to handle something.

**When this happens:**
- The agent needs to "think" or generate text
- The agent calls the Large Language Model (LLM) for reasoning
- This can happen **multiple times** during one agent's turn
- The agent is asking: "How should I respond?" or "What should I do?"

**Real Example:**
```
📞 on_llm_start: WeatherAgent asking "How should I get weather for user's location?"
```

#### 4. `on_llm_end` - Agent Gets AI Response 🧠✨
```python
async def on_llm_end(context, agent, response):
    print(f"🧠✨ Agent {agent.name} got AI response: {response}")
```

**🏭 Factory Worker Analogy**: Like the expert consultant hanging up after giving advice. The worker now knows what to do next.

**When this happens:**
- The LLM has finished "thinking" and provided a response
- The agent now has guidance on what to do next
- This always follows an `on_llm_start`
- The agent might use this advice to take action or call tools

**Real Example:**
```
🧠✨ on_llm_end: AI responds "Use the weather_lookup tool for their location"
```

#### 5. `on_tool_start` - Agent Uses a Tool 🔨
```python
async def on_tool_start(context, agent, tool):
    print(f"🔨 Agent {agent.name} is using tool: {tool.name}")
```

**🏭 Factory Worker Analogy**: Like a worker reaching for a specific tool from their toolbox to accomplish a task.

**When this happens:**
- The agent needs to perform a specific action
- The agent calls an external function/API/database
- This can happen **multiple times** during one agent's turn
- Common tools: database_lookup, send_email, web_search, calculator

**Real Example:**
```
🔨 on_tool_start: WeatherAgent using "weather_lookup" tool
```

#### 6. `on_tool_end` - Agent Finishes Using Tool ✅🔨
```python
async def on_tool_end(context, agent, tool, result):
    print(f"✅🔨 Agent {agent.name} finished using {tool.name}. Result: {result}")
```

**🏭 Factory Worker Analogy**: Like a worker putting the tool back and seeing the result of their work.

**When this happens:**
- The tool has completed its work and returned a result
- The agent now has new information to work with
- This always follows an `on_tool_start`
- The agent might use this result to make decisions or call the LLM again

**Real Example:**
```
✅🔨 on_tool_end: weather_lookup returned "72°F, Sunny, New York"
```

#### 7. `on_handoff` - Agent Receives Control 🏃‍♂️➡️🏃‍♀️
```python
async def on_handoff(context, agent, source):
    print(f"🏃‍♂️➡️🏃‍♀️ Agent {agent.name} received handoff from {source.name}")
```

**🏭 Factory Worker Analogy**: Like one worker finishing their part of an assembly line and passing the work to the next specialized worker.

**When this happens:**
- Another agent has decided this agent should take over
- Work is being transferred from one agent to another
- The receiving agent becomes the new active agent
- This often happens when specialized expertise is needed

**Real Example:**
```
🏃‍♂️➡️🏃‍♀️ TechnicalSupport received handoff from CustomerService
(Customer issue was too complex for general support)
```

## Complete Lifecycle Flow Example 🔄

Here's what a typical conversation looks like with ALL hooks:

```
User: "What's the weather in New York?"

🕘 on_start
   └─ WeatherAgent: "I'm now responsible for this weather question"

📞 on_llm_start  
   └─ WeatherAgent: "AI, how should I handle a weather request?"

🧠✨ on_llm_end
   └─ AI: "Use the weather_lookup tool for New York"

🔨 on_tool_start
   └─ WeatherAgent: "Using weather_lookup tool"

✅🔨 on_tool_end  
   └─ Tool result: "72°F, Sunny, New York"

📞 on_llm_start (again!)
   └─ WeatherAgent: "AI, how should I format this weather data?"

🧠✨ on_llm_end
   └─ AI: "Say: 'It's 72°F and sunny in New York!'"

✅ on_end
   └─ WeatherAgent: "Task complete!"
```

## Understanding the Difference: `on_start` vs `on_llm_start` 🤔

This is the **most confusing part** for beginners! Let's clarify:

**🕘 `on_start` - Agent Becomes Responsible**
- Happens **once** when agent takes control
- The agent is now "in charge" of the conversation
- Like clocking in at work

**📞 `on_llm_start` - Agent Needs to Think**
- Can happen **multiple times** during agent's turn
- The agent asks the AI brain for help with reasoning
- Like calling an expert consultant for advice

**Timeline Example:**
```
🕘 on_start: "I'm now handling this customer question"
📞 on_llm_start: "AI, how should I respond to this?"
✅ on_llm_end: AI gives advice
🔧 on_tool_start: Use a tool based on AI advice  
📞 on_llm_start: "AI, how should I format the results?"
✅ on_llm_end: AI gives formatting advice
🏁 on_end: "Task complete!"
```

**Key Difference:** An agent **starts once** but might **ask the AI multiple times**.

## Simple Example

```python
from openai_agents import Agent, AgentHooksBase

# Create a custom hook class for our agent
class MyAgentHooks(AgentHooksBase):
    async def on_start(self, context, agent):
        print(f"🕘 {agent.name} is starting up!")
    
    async def on_llm_start(self, context, agent, system_prompt, input_items):
        print(f"📞 {agent.name} is asking AI for help")
    
    async def on_llm_end(self, context, agent, response):
        print(f"🧠✨ {agent.name} got AI response")
    
    async def on_tool_start(self, context, agent, tool):
        print(f"🔨 {agent.name} is using {tool.name}")
    
    async def on_tool_end(self, context, agent, tool, result):
        print(f"✅ {agent.name} finished using {tool.name}")
    
    async def on_end(self, context, agent, output):
        print(f"🎉 {agent.name} completed the task!")

# Create an agent
my_agent = Agent(
    name="Helper Bot",
    # ... other agent configuration
)

# Attach our hooks to this specific agent
my_agent.hooks = MyAgentHooks()
```

## Advanced Example with Detailed Tracking

```python
from openai_agents import Agent, AgentHooksBase
import time
from datetime import datetime

class DetailedAgentHooks(AgentHooksBase):
    def __init__(self):
        self.start_time = None
        self.llm_calls = 0
        self.tool_calls = 0
    
    async def on_start(self, context, agent):
        self.start_time = time.time()
        self.llm_calls = 0
        self.tool_calls = 0
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"🕘 [{timestamp}] {agent.name} became active")
    
    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.llm_calls += 1
        print(f"📞 LLM Call #{self.llm_calls}: {agent.name} asking AI for guidance")
        print(f"   Input: {len(input_items)} items to think about")
    
    async def on_llm_end(self, context, agent, response):
        print(f"🧠✨ LLM Call #{self.llm_calls} completed")
        print(f"   AI response length: {len(str(response))} characters")
    
    async def on_tool_start(self, context, agent, tool):
        self.tool_calls += 1
        print(f"🔨 Tool #{self.tool_calls}: {agent.name} using {tool.name}")
    
    async def on_tool_end(self, context, agent, tool, result):
        print(f"✅🔨 Tool #{self.tool_calls} completed")
        print(f"   Result preview: {str(result)[:50]}...")
    
    async def on_handoff(self, context, agent, source):
        print(f"🏃‍♂️➡️🏃‍♀️ {agent.name} received work from {source.name}")
        print(f"   Work is being transferred due to specialization")
    
    async def on_end(self, context, agent, output):
        duration = time.time() - self.start_time if self.start_time else 0
        print(f"✅ {agent.name} FINISHED in {duration:.2f} seconds")
        print(f"📊 Total: {self.llm_calls} AI calls, {self.tool_calls} tool uses")
        print(f"🎯 Final result: {str(output)[:100]}...")

# Use it with your agent
customer_service = Agent(name="CustomerService")
customer_service.hooks = DetailedAgentHooks()
```

## Hook Execution Order 📋

Understanding the order hooks fire helps with debugging:

```
1. 🕘 on_start (agent becomes active)
2. 📞 on_llm_start (may happen multiple times)
3. 🧠✨ on_llm_end (matches each llm_start)
4. 🔨 on_tool_start (may happen multiple times)  
5. ✅🔨 on_tool_end (matches each tool_start)
6. 🏃‍♂️➡️🏃‍♀️ on_handoff (if agent hands off work)
7. ✅ on_end (agent finishes - only if no handoff)
```

**Note:** Steps 2-5 can repeat multiple times and in different orders!

## Real-World Use Cases 🌍

### 1. Debugging & Development 🐛
```python
class DebugHooks(AgentHooksBase):
    async def on_start(self, context, agent):
        print(f"🐛 {agent.name} is now active")
    
    async def on_llm_start(self, context, agent, system_prompt, input_items):
        print(f"🐛 {agent.name} LLM input: {input_items}")
    
    async def on_tool_start(self, context, agent, tool):
        print(f"🐛 {agent.name} using tool: {tool.name}")
```

### 2. User Experience Updates 👥
```python
class UserFriendlyHooks(AgentHooksBase):
    async def on_start(self, context, agent):
        print(f"👋 {agent.name} is helping you...")
    
    async def on_tool_start(self, context, agent, tool):
        if tool.name == "database_lookup":
            print("🔍 Looking up your information...")
        elif tool.name == "send_email":
            print("📧 Sending confirmation email...")
    
    async def on_end(self, context, agent, output):
        print("✅ All done!")
```

## Key Points for Beginners 📚

1. **Agent-Specific**: These hooks only watch **one specific agent**
2. **Individual Monitoring**: Each agent can have its own set of hooks
3. **Attachment**: You attach hooks using `agent.hooks = YourHooksClass()`
4. **Async**: All hook methods must be `async` functions
5. **Multiple Calls**: `on_llm_start` and `on_tool_start` can happen multiple times
6. **Paired Events**: Every `start` hook has a corresponding `end` hook

## Common Mistakes ❌

### ❌ Don't Do This:
```python
# Forgetting async
def on_start(self, context, agent):  # Missing 'async'!
    print("Started")

# Trying to return values from hooks
async def on_tool_end(self, context, agent, tool, result):
    return "modified result"  # Hooks don't return values!
```

### ✅ Do This Instead:
```python
async def on_start(self, context, agent):  # Proper async
    print("Started")

async def on_tool_end(self, context, agent, tool, result):
    # Just observe and log, don't try to modify
    print(f"Tool {tool.name} returned: {result}")
```

Think of Agent hooks as putting a **personal assistant** next to each of your agents to take detailed notes on everything they do! 📝👨‍💼
