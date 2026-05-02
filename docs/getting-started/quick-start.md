# Quick Start

Get kairix running and searching your documents in under 30 minutes.

## What you need

- **Docker and Docker Compose** (Docker Desktop, or Docker Engine + Compose plugin)
- **An LLM API key** — Azure OpenAI, standard OpenAI, or any OpenAI-compatible provider
- **A folder of documents** — markdown files, text files, or structured notes

## Steps

### 1. Get the compose file

```bash
curl -O https://raw.githubusercontent.com/quanyeomans/kairix/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/quanyeomans/kairix/main/.env.example
```

Or clone the full repo:

```bash
git clone https://github.com/quanyeomans/kairix
cd kairix
```

### 2. Set up your credentials

```bash
cp .env.example .env
```

Open `.env` and add your LLM API key:

```bash
# For Azure OpenAI:
KAIRIX_LLM_ENDPOINT=https://your-resource.openai.azure.com
KAIRIX_LLM_API_KEY=your-key-here

# Or for standard OpenAI / OpenRouter:
# KAIRIX_LLM_ENDPOINT=https://api.openai.com/v1
# KAIRIX_LLM_API_KEY=sk-your-key-here
```

### 3. Point to your documents

```bash
ln -s ~/Documents/my-notes ./documents
```

**Don't have documents ready?** The container includes 5,800+ curated reference library documents. You can start searching immediately and add your own documents later.

### 4. Start everything

```bash
docker compose up -d
```

This starts three services:
- **kairix** — search engine and MCP server (port 8080)
- **kairix-worker** — indexes your documents automatically every hour
- **neo4j** — knowledge graph for people/company queries

### 5. Index your documents

```bash
docker compose exec kairix kairix embed
```

This indexes your documents for search. For 1,000 documents (~4,000 chunks), expect ~$0.50-1.00 with text-embedding-3-large.

### 6. Verify your setup

```bash
docker compose exec kairix kairix onboard check
```

You should see:

```
kairix deployment check
──────────────────────────────────────────────────
  ✓ kairix_on_path
  ✓ wrapper_installed
  ✓ secrets_loaded
  ✓ document_root_configured — Document root: /data/documents
  ✓ vector_search_working
  ✓ neo4j_reachable
  ✓ agent_knowledge_populated
  ✓ chunk_date_populated
  ✓ mcp_service
──────────────────────────────────────────────────
  All 9 checks passed
```

If any checks fail, the output explains what to fix.

### 7. Verify search quality

Run the built-in benchmark against the reference library:

```bash
docker compose exec kairix kairix eval
```

This indexes the reference library (5,800+ open-source documents), runs a 200-case gold suite, and reports search quality scores. Expected baseline:

| Metric | Expected |
|--------|----------|
| Weighted total | ≥ 0.80 |
| NDCG@10 | ≥ 0.90 |
| Hit@5 | ≥ 95% |

If scores are significantly below these, check your embedding model and LLM connection.

### 8. Search

```bash
docker compose exec kairix kairix search "your question here"
```

Your knowledge store is running.

---

## Connecting agents

The MCP server runs on port 8080. Any MCP-compatible agent can connect via SSE.

**Claude Desktop / Claude Code:**

Add to your MCP config:
```json
{
  "mcpServers": {
    "kairix": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

See [connecting-agents.md](connecting-agents.md) for OpenClaw, LangGraph, and other platforms.

---

## What happens next

- **Documents are indexed automatically** every hour by the worker service
- **The MCP server** exposes 7 tools: search, entity, prep, timeline, research, contradict, usage_guide
- **Run `kairix onboard check`** any time to verify everything is working
- **Run `kairix eval`** to benchmark search quality against the reference library
