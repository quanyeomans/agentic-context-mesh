# Prior Art & Inspirations

This document acknowledges the search, retrieval, and agent-memory implementations that influenced the design of `qmd-azure-embed` and the broader [Mnemosyne](https://github.com/quanyeomans/agentic-context-mesh) retrieval architecture it feeds into.

We've reviewed these systems as primary references. Where we've adopted a pattern directly, we've said so.

---

## QMD — Local Search Engine for Docs

**Author:** [tobi](https://github.com/tobi) (Tobias Lütke)  
**Repo:** https://github.com/tobi/qmd  
**Licence:** MIT  

The foundation of our local retrieval stack. QMD combines BM25 full-text search, vector semantic search, and LLM reranking in a single CLI tool designed for agentic workflows (`--json`, `--files` output modes, MCP server support). Its hierarchical context system (per-collection context files help LLMs make routing decisions) is underutilised in most deployments.

**What we took:** The entire search architecture. `qmd-azure-embed` exists to replace QMD's local embedding pipeline with Azure OpenAI on CPU-constrained hosts where the llama.cpp backend runs out of memory or too slowly for practical use. We write directly into QMD's SQLite schema so the rest of the tool chain (BM25, MCP server, vsearch) works unchanged.

**What we diverged on:** QMD's vsearch loads a llama.cpp reranker model for query expansion, which hangs indefinitely on CPU-only deployments. Our recall gate bypasses this and queries vectors_vec directly via sqlite-vec's MATCH syntax. We've documented this constraint in [QMD_COMPAT.md](QMD_COMPAT.md).

---

## sqlite-vec

**Author:** [Alex Garcia](https://github.com/asg017)  
**Repo:** https://github.com/asg017/sqlite-vec  
**Licence:** Apache 2.0 / MIT  

A SQLite extension for storing and querying float32 vectors using cosine distance. Ships as a loadable `.so` extension. QMD uses it as its vector storage backend — we piggyback on the same tables.

**What we took:** The vec0 virtual table as storage, the `MATCH ? AND k=N ORDER BY distance` query pattern for recall checks and direct vector search.

**What we learned the hard way:** sqlite-vec virtual tables do not support `INSERT OR REPLACE` or `INSERT OR IGNORE` — conflict clauses are silently rejected and raise `OperationalError`. The correct upsert is `DELETE WHERE hash_seq = ?` followed by `INSERT`. We now use a staging table approach: write to a normal `TEMPORARY` SQLite table (which supports all conflict clauses), then merge into vectors_vec in bulk via `DELETE IN (SELECT ...) + INSERT SELECT`. This is documented and tested in `tests/integration/test_sqlite_vec_constraints.py`.

---

## OpenViking — Context Database for AI Agents

**Author:** [Volcengine](https://github.com/volcengine) (ByteDance's cloud division)  
**Repo:** https://github.com/volcengine/OpenViking  
**Licence:** Apache 2.0  

Open-source context database designed for AI agents. The key concept is a **filesystem paradigm for memory**: memories, resources, and skills organised hierarchically and navigated via `viking://` URI protocol. Agents browse summaries first and only load full documents when needed.

**What influenced us:**  
The **L0/L1/L2 tiered loading model** — documents are indexed at three verbosity levels:
- L0: ~100-token abstract
- L1: ~2,000-token structural overview  
- L2: Full document

This directly informs Mnemosyne's planned Phase 1 tiered loading system. The insight is sound: loading full documents for every retrieval wastes tokens and degrades context quality. Agents should navigate summaries first.

The **retrieval trajectory visualisation** — showing which folders the agent traversed and why it selected a document — addresses the RAG black-box problem. We've adopted this philosophy in our benchmark scoring methodology: we want observable retrieval, not just a ranked list.

**What we didn't take:**  
We're not adopting OpenViking directly. Our stack (Obsidian vault + scoped QMD collections + sqlite-vec) already provides hierarchical structure. OpenViking's Volcengine cloud backend is a data sovereignty concern for some deployment contexts. We're building equivalent tiered loading natively on our existing infrastructure.

---

## mem0

**Author:** [mem0ai](https://github.com/mem0ai)  
**Repo:** https://github.com/mem0ai/mem0  
**Licence:** Apache 2.0  

Persistent memory layer for AI agents with automatic extraction, deduplication, and contextual retrieval across sessions. Supports multiple vector store backends (Qdrant, Chroma, Pinecone, etc.) and memory scoping (user-level, agent-level, session-level).

**What influenced us:**  
The **memory scoping model** — separating what belongs to a user, an agent, and a session. Our vault collection structure (`knowledge-shared`, `knowledge-builder`, `shape-memory`, etc.) implements the same partitioning without a separate memory service. The scoping is structural (directory + collection) rather than metadata-tagged, which is simpler to maintain.

The **deduplication approach** — mem0 runs an LLM over incoming memories to detect near-duplicates before writing. We don't implement this yet, but it's on the Mnemosyne Phase 2 backlog (vault compaction with semantic deduplication rather than the current time-based approach).

---

## Lilian Weng — LLM Powered Autonomous Agents

**Author:** [Lilian Weng](https://lilianweng.github.io) (OpenAI)  
**Post:** https://lilianweng.github.io/posts/2023-06-23-agent/  

The canonical technical reference on agent architecture. Defines agents as LLM + memory + planning + tool use. Covers long-term memory design (external vector stores, associative memory), task decomposition patterns (CoT, ToT), and self-reflection mechanisms (ReAct, Reflexion).

**What influenced us:**  
The **memory taxonomy** — sensory (input context), short-term (in-context), long-term (external, vector store). This framing is the foundation of Mnemosyne's architecture: the vault is long-term memory, daily session logs are short-term episodic memory, and in-context MEMORY.md pointers are the bridge between them.

The **limitations framing** — finite context, long-term planning reliability, and natural language interface reliability remain the core engineering challenges in production agents. Our benchmark design (50 queries across 6 categories including temporal, multi-hop, and conceptual) is a direct operationalisation of these failure modes.

---

## Obsidian as a Knowledge Layer

**Author:** [Dynalist Inc.](https://obsidian.md) (Erica Xu, Shida Li)  
**Product:** https://obsidian.md  
**Licence:** Proprietary (free for personal use)  

Markdown-based local knowledge base with a plugin ecosystem and a rich linking model (backlinks, graph view, Dataview queries).

**What influenced us:**  
Using Obsidian as the **persistent memory substrate** for AI agents — not just as a note-taking tool. The vault structure provides the hierarchical organisation that makes QMD's collection scoping meaningful. Obsidian's bidirectional linking model informs how we think about knowledge graphs in Phase 2 of Mnemosyne.

The combination of **Obsidian + QMD** as a retrieval stack was first observed gaining traction in the the community ([Reddit/ObsidianMD, 2025](https://www.reddit.com/r/ObsidianMD/comments/1rix34w/)) and validated as a pattern worth building on.

---

## Onyx — Open-Source Enterprise Search

**Author:** [Onyx (formerly Danswer)](https://github.com/onyx-dot-app/onyx)  
**Repo:** https://github.com/onyx-dot-app/onyx  
**Licence:** MIT  

Open-source enterprise search and AI assistant with configurable knowledge sources, custom agents, collaboration features, and a security posture (single approved GenAI interface, trusted LLM providers only).

**What influenced us:**  
The **single approved interface** architecture — rather than each agent maintaining its own retrieval stack, a shared search layer is queried by all agents. This is the direction Mnemosyne is moving: a shared retrieval service that all AI agents call, rather than per-agent vector stores.

The **knowledge source configuration model** — declarative indexing of heterogeneous sources (Confluence, Notion, Google Drive, Slack, etc.) with per-source sync policies. Mnemosyne Phase 2 will implement a similar connector model for non-vault sources.

---

## Prior Internal Platform — Superseded

**Authors:** Internal (operational fork)
**Status:** Superseded by the agent stack (2026-03-07)  

Our previous Python multi-agent platform built on CrewAI + LangGraph + n8n. The platform reached a mature state and embodied significant domain-specific intelligence before being superseded.

**What we preserved:**  
The **research synthesis pipeline** — a 4-stage sequential pipeline (Discovery → Analysis → Synthesis → Output) for producing structured briefings from unstructured knowledge. Highly engineered prompts with evidence-grading and audience-specific framing. This pipeline's prompt templates and task descriptions are preserved in the vault under `04-Agent-Knowledge/shared/platform-analysis.md`.

The **parallel specialist pattern** — multiple specialist agents analyse the same research simultaneously, then a coordinator synthesises. A/B tested against sequential pipeline with equivalent quality. The separation-of-concerns framing produces better-evidenced outputs.

**Lesson:** Domain-specific intelligence (how to research a topic, how to frame findings, how to structure a briefing) travels across architectures. The infrastructure changed; the prompts remain valuable.

---

## n8n — Workflow Automation

**Author:** [n8n GmbH](https://github.com/n8n-io)  
**Repo:** https://github.com/n8n-io/n8n  
**Licence:** Fair Code (self-host free, cloud paid)  

Visual/low-code workflow automation with 500+ integrations, human-in-the-loop approval steps, and MCP server support.

**What influenced us:**  
The **MCP server capability** — n8n workflows callable as tools from other AI systems. This bidirectional pattern (agents call workflows; workflows call agents) is how we think about the agent ↔ automation layer integration.

The **production-readiness gap** documentation — the community-documented gap between "it works locally" and "it's production-ready" (external PostgreSQL, queue mode, HTTPS, monitoring, backups) informed our reliability requirements for `qmd-azure-embed`. We applied the same rigour: resumable on failure, atomic batch writes, run logging, recall gate.

---

## Andrew Ng — Agentic Design Patterns

**Author:** [Andrew Ng](https://www.andrewng.org) / DeepLearning.AI  
**Source:** [Agentic Design Patterns PDF](https://drive.google.com/file/d/1-5ho2aSZ-z0FcW8W_jMUoFSQ5hTKvJ43/view)  

Four foundational agentic design patterns: Reflection (self-critique and iteration), Tool Use (function calling), Planning (multi-step decomposition), Multi-Agent Collaboration (specialised agents coordinating).

**What influenced us:**  
The **Reflection pattern** applied to retrieval: after every embedding run, we reflect (recall gate) on whether the system still behaves correctly. This is self-critique applied at the infrastructure level, not just the reasoning level.

---

## "AI Autonomy is a Human Hallucination" / "Ease Out"

**Author:** [Humanity Lesson for AI](https://humanitylessonforai.substack.com)  
**Posts:** 
- https://humanitylessonforai.substack.com/p/ai-autonomy-is-a-human-hallucination
- https://humanitylessonforai.substack.com/p/ease-out-why-we-should-not-worry

Critical perspective on multi-agent systems: semantic drift in agent-to-agent communication, observability costs growing non-linearly, and diseconomy of scale. Recommends prioritising single-agent systems with internal memory over peer-to-peer multi-agent orchestration.

**What influenced us:**  
The **observability cost** argument directly shaped Mnemosyne's scope. Rather than a separate vector service, a separate memory agent, and a separate retrieval agent, we keep everything in QMD's SQLite index — a single observable artefact. The "single-agent with internal memory" preference is reflected in the platform's architecture (one main agent delegates via subagents; the vault is shared memory, not a separate agent).

---

## Summary Table

| System | Key concept taken | Where it appears |
|---|---|---|
| QMD (tobi) | Full search architecture | `qmd-azure-embed` target store |
| sqlite-vec (asg017) | vec0 constraints (no OR REPLACE, MATCH syntax) | `test_sqlite_vec_constraints.py`, staging table pattern |
| OpenViking (Volcengine) | L0/L1/L2 tiered loading | Mnemosyne Phase 1 architecture |
| mem0 (mem0ai) | Memory scoping model, deduplication | Vault collection structure, Phase 2 backlog |
| Lilian Weng (OpenAI) | Memory taxonomy (sensory/short-term/long-term) | Mnemosyne architecture framing |
| Obsidian (Dynalist) | Vault as memory substrate | Entire retrieval stack foundation |
| Onyx | Single shared search layer | Mnemosyne Phase 2 direction |
| Prior platform (internal) | Research synthesis pipeline | Vault `platform-analysis.md` |
| n8n | Production-readiness checklist | Reliability design of `qmd-azure-embed` |
| Andrew Ng | Reflection pattern for retrieval | Post-embed recall gate |
| Humanity Lesson for AI | Single observable artefact > distributed agents | Mnemosyne scope decisions |
