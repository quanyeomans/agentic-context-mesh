# Quick Start

Get kairix running and searching your documents in under 30 minutes.

## What you need

- **Docker and Docker Compose** (Docker Desktop, or Docker Engine + Compose plugin)
- **An LLM API key** — Azure OpenAI or standard OpenAI
- **A folder of documents** — markdown files, text files, or an Obsidian vault

## Steps

### 1. Get the code

```bash
git clone https://github.com/quanyeomans/kairix
cd kairix

# Optional: pin to a specific release
# git checkout v1.0.0
```

### 2. Set up your credentials

```bash
cp .env.example .env
```

Open `.env` in your editor and add your LLM API key:

```bash
# For Azure OpenAI:
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your-key-here

# Or for standard OpenAI:
# OPENAI_API_KEY=sk-your-key-here
```

### 3. Point to your documents

Copy or symlink your document folder into the repo:

```bash
# If your documents are in ~/Documents/my-vault:
ln -s ~/Documents/my-vault ./vault

# Or copy them:
# cp -r ~/Documents/my-vault ./vault
```

### 4. Start everything

```bash
docker compose up -d
```

This starts three services:
- **kairix** — the search engine and MCP server (port 8080)
- **kairix-worker** — automatically indexes your documents every hour
- **neo4j** — knowledge graph for people/company queries (optional but recommended)

### 5. Run the setup wizard

```bash
docker compose exec kairix kairix setup
```

The wizard walks you through:
1. Verifying your LLM connection
2. Confirming your document source
3. Choosing a search configuration (consulting, technical, or general)
4. Running the initial index

This takes about 5 minutes.

> **Cost note:** Embedding uses your LLM API key. For 1,000 documents (~4,000 chunks), expect ~$0.50-1.00 with text-embedding-3-large. The reference library (~14,000 chunks) costs ~$2-3 to embed initially. Incremental updates are much cheaper.

### 6. Search

```bash
docker compose exec kairix kairix search "your question here"
```

That's it. Your knowledge base is running.

---

## What happens next

- **Documents are indexed automatically** every hour by the worker service
- **The MCP server** runs on port 8080 — connect any MCP-compatible agent (Claude, OpenClaw, etc.)
- **Verify the server is running**: `curl http://localhost:8080/sse` should return an SSE stream header
- **Run diagnostics**: `docker compose exec -it kairix kairix onboard check`
- **Run quality checks** anytime: `docker compose exec kairix kairix onboard check`

## Connecting agents

Any MCP-compatible agent can connect to kairix at `http://localhost:8080`. Available tools:

| Tool | What it does |
|------|-------------|
| `search` | Find answers in your knowledge base |
| `research` | Search iteratively for complex questions |
| `entity` | Look up a specific person, company, or topic |
| `prep` | Get a quick summary of a topic |
| `timeline` | Inspect how date-based questions are interpreted |
| `usage_guide` | Get help using kairix tools |

## Measuring search quality

Want to know how well kairix works on your documents? Build a test suite:

```bash
# Create test queries + relevance judgments from your data
docker compose exec kairix kairix eval build-gold \
  --suite queries.yaml --output gold.yaml

# Test different search configurations
docker compose exec kairix kairix eval hybrid-sweep \
  --suite gold.yaml
```

## Stopping and restarting

```bash
docker compose down        # Stop (data preserved in volumes)
docker compose up -d       # Start again
docker compose logs -f     # Watch logs
```

## Rebuilding after code changes

```bash
docker compose down       # Stop all services
docker compose build      # Rebuild images
docker compose up -d      # Start again
```

## Without Docker

If you prefer to run kairix directly:

```bash
pip install "kairix[neo4j,agents]"
kairix setup
kairix embed
kairix search "your question"
```

See [OPERATIONS.md](../OPERATIONS.md) for full deployment details.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "No module named 'kairix'" | Run `docker compose build` to rebuild the image |
| "Connection refused" on search | Wait 30 seconds for services to start, then try again |
| "0 results" on search | Run `docker compose exec kairix kairix embed` to index documents |
| Neo4j won't start | Check `NEO4J_PASSWORD` in `.env` — it must be at least 8 characters |
| "Rate limited" during embed | The system retries automatically — wait a few minutes |
