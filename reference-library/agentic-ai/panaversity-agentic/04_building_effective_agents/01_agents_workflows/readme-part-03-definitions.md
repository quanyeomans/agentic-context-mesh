## Definitions

### AI Workflows

&#x20;*Example of a prompt-chaining **workflow** pattern, where a task is broken into sequential LLM calls with an intermediate **“gate”** check to verify progress before proceeding.*

**AI workflows** are systems in which one or more LLM calls are orchestrated through **predefined code paths** and sequences of steps. In a workflow, the developer explicitly defines the sequence of sub-tasks or decision branches – for example first generating an outline, then validating it, then expanding it – and the LLM is called at each step with specific prompts. This yields a *prescriptive, deterministic flow*: the logic of how to solve the overall task is hard-coded by the developer. Workflows emphasize **predictability and consistency**: given the same inputs, they follow the same scripted procedure every time. The LLM in a workflow is typically *augmented* with tools or retrieval, but it does not decide *which* step to take next – the code orchestrates that. Because the path is fixed, workflows are well-suited for tasks that can be **cleanly decomposed** or categorized in advance.

### AI Agents

&#x20;*Diagram of an **autonomous agent** loop: the agent iteratively plans actions, invokes tools or external APIs, observes results, and adjusts its plan until the task is complete (or a stopping condition is reached).*

**AI agents**, in contrast, are **autonomous LLM-based systems** where the LLM itself **dynamically controls its own sequence of actions and tool usage**. An agent typically receives a high-level goal or command from a human, then internally **plans a strategy** and acts in a loop without a fixed script. At each step the agent decides what to do next – e.g. whether to call a tool, retrieve information, ask a follow-up question, or finalize an answer – based on the current context and some prompting that encourages planning. Crucially, the agent uses **environment feedback** (results of tool calls, code execution outputs, etc.) after each action to inform its next step. This feedback loop continues until the agent decides it has achieved the goal or hits a pre-defined stopping condition. In summary, agents offer **flexibility and model-driven decision-making**: the LLM has the freedom to handle unexpected situations or adapt the plan on the fly, rather than following a pre-coded path.