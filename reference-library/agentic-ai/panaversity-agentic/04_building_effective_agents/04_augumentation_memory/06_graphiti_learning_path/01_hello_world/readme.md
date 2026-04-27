---
title: "Step 01: Hello World - Your First Graphiti Program"
source: Panaversity Learn Agentic AI
source_url: https://github.com/panaversity/learn-agentic-ai
licence: Apache-2.0
domain: agentic-ai
subdomain: panaversity-agentic
date_added: 2026-04-25
---

# Step 01: Hello World - Your First Graphiti Program

Welcome to Graphiti! In this step, you'll create your first temporal knowledge graph and see how it works.

## 📚 Official Documentation

- [Graphiti Getting Started](https://help.getzep.com/graphiti/getting-started/welcome) - Official introduction to Graphiti
- [Quick Start Guide](https://help.getzep.com/graphiti/getting-started/quick-start) - Quick Setup Graphiti Core
- [Gemini Config](https://help.getzep.com/graphiti/configuration/llm-configuration)

## 🎯 What You'll Learn

By the end of this step, you will:

- Install and set up Graphiti
- Add your first episode to create a knowledge graph
- Search the graph to retrieve information
- Understand the basic Graphiti workflow: text → graph → search

## 📋 Prerequisites

- Python 3.10+ installed
- Neo4j database running (local or AuraDB)
- Gemini API key
- Basic Python knowledge

## 🛠️ Setup

### 1. Setup Project and Install Graphiti

```bash
uv init hello_tkg

uv add "graphiti-core[google-genai]"
```

### 2. Environment Variables

Create a `.env` file or export these variables:

```bash
GEMINI_API_KEY="your-openai-api-key"
NEO4J_URI="neo4j://localhost:7687"  # or your AuraDB URI
NEO4J_USER="neo4j"
NEO4J_PASSWORD="your-neo4j-password"
SEMAPHORE_LIMIT=5
```

## 🚀 Hello World Example

Let's create your first Graphiti program that adds a simple episode and verifies it works:

### hello_graphiti.py

1. Import libraries required

```python
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from logging import INFO

from dotenv import load_dotenv, find_dotenv

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF
```

2. Imports for Gemini Setup 
```python
from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
```

3. Set up logging and environment variables for connecting to the Neo4j database:

```python
# Configure logging
logging.basicConfig(
    level=INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

load_dotenv(find_dotenv())

# Neo4j connection parameters
# Make sure Neo4j Desktop is running with a local DBMS started
neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
neo4j_user = os.environ.get('NEO4J_USER', 'neo4j')
neo4j_password = os.environ.get('NEO4J_PASSWORD', 'password')
if not neo4j_uri or not neo4j_user or not neo4j_password:
    raise ValueError('NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD must be set')

gemini_api_key = os.environ.get('GEMINI_API_KEY')
if not gemini_api_key:
    raise ValueError('GEMINI_API_KEY must be set')
```


4. Create an async main function to run all Graphiti operations:

```python
async def main():
    # Main function implementation will go here
    pass

if __name__ == '__main__':
    asyncio.run(main())

```

5. Connect to Neo4j and set up Graphiti indices. This is required before using other Graphiti functionality:

```python
    # Initialize Graphiti with Neo4j connection
    graphiti = Graphiti(neo4j_uri, neo4j_user, neo4j_password,
                            llm_client=GeminiClient(
        config=LLMConfig(
            api_key=gemini_api_key,
            model="gemini-2.0-flash"
        )
    ),
    embedder=GeminiEmbedder(
        config=GeminiEmbedderConfig(
            api_key=gemini_api_key,
            embedding_model="embedding-001"
        )
    ),
    cross_encoder=GeminiRerankerClient(
        config=LLMConfig(
            api_key=gemini_api_key,
            model="gemini-2.0-flash-exp"
        )
    )

                        )

    try:
        # Initialize the graph database with graphiti's indices. This only needs to be done once.
        await graphiti.build_indices_and_constraints()
        
        # Additional code will go here

        
    finally:
        # Close the connection
        await graphiti.close()
        print('\nConnection closed')

        pass

```

6. Adding Episodes and basic search
- The simplest way to retrieve relationships (edges) from Graphiti is using the search method, which performs a hybrid search combining semantic similarity and BM25 text retrieval. 

```python
        print("📝 Adding your first episode...")
        await graphiti.add_episode(
            name="hello_world_episode",
            episode_body=(
                "Today I started learning Graphiti, a powerful Python framework for "
                "building temporal knowledge graphs. Graphiti helps AI agents remember "
                "information over time and understand how relationships evolve."
            ),
            source=EpisodeType.text,
            source_description="Learning journal entry",
            reference_time=datetime.now(),
        )

        print("✅ Episode added successfully!")

        # Verify the graph has data
        print("🔍 Verifying the knowledge graph...")

        # Search for information about Graphiti
        search_results: list[EntityEdge] = await graphiti.search(
            query="What is Graphiti?",
            num_results=3
        )


        print(f"🎉 Found {len(search_results)} results")

        # data nodes
        for i, data in enumerate(search_results):
            print(f"  {i}:\nUUID: {data.episodes}")
            print(f"  Fact: {data.fact}")
            print("\n")
```

## ▶️ Running Your First Program

1. **Save the code** as `main.py`
2. **Set your environment variables** (see Setup section)
3. **Run the program**:

```bash
uv run python main.py
```

## 📊 Expected Output

You should see something like:

```
🚀 Starting Hello World Graphiti Example...
🔧 Building initial graph structure...
📝 Adding your first episode...
✅ Episode added successfully!
🔍 Verifying the knowledge graph...
🎉 Success! Found 3 nodes and 2 edges
📊 Graph Contents:
    ...
🔒 Graphiti client closed.
```

Challenge: [Do Node Search](https://help.getzep.com/graphiti/getting-started/quick-start#node-search-using-search-recipes)

## 🔍 What Just Happened?

Let's break down what your first Graphiti program did:

### 1. **Client Setup**

```python
client = Graphiti(uri=..., user=..., password=...)
```

- Connected to your Neo4j database
- Initialized the Graphiti framework

### 2. **Graph Structure Setup**

```python
await client.build_indices_and_constraints()
```

- Created necessary database indices for performance
- Set up constraints for data integrity

### 3. **Episode Addition**

```python
await client.add_episode(...)
```

- Added text content to the knowledge graph
- Graphiti automatically extracted entities and relationships
- Created nodes for concepts like "Graphiti", "Python framework", etc.
- Created edges showing relationships between concepts

### 4. **Knowledge Retrieval**

```python
search_results = await client.search(query="What is Graphiti?")
```

- Performed semantic search on the knowledge graph
- Retrieved relevant nodes and edges
- Demonstrated that information was successfully stored and can be retrieved

**Key Point**: Text goes in → Graphiti processes it → Structured knowledge comes out that you can search!

## 🧪 Try It Yourself

### Experiment 1: Add More Content

Modify the episode body to include different information:

```python
episode_body = (
    "I'm building a TutorsGPT system that needs to remember student interactions. "
    "The system should track learning progress, personalize content, and adapt "
    "to individual student needs over time."
)
```

### Experiment 2: Search Different Queries

Try different search queries:

```python
# Search for specific concepts
search_results = await client.search(query="student learning progress")

# Search for relationships
search_results = await client.search(query="what adapts to student needs?")
```

### Experiment 3: Add Multiple Episodes

Try adding multiple episodes and see how they connect:

```python
# Episode 1
await client.add_episode(
    name="alice_starts",
    episode_body="Alice started learning Python programming",
    reference_time=datetime.now() - timedelta(days=7)
)

# Episode 2
await client.add_episode(
    name="alice_progress",
    episode_body="Alice completed her first Python project successfully",
    reference_time=datetime.now()
)

# Search for Alice's journey
results = await client.search("Tell me about Alice's Python learning")
```

## ✅ Verification Checklist

- [ ] Graphiti installed successfully
- [ ] Environment variables configured
- [ ] Neo4j database accessible
- [ ] Program runs without errors
- [ ] Knowledge graph contains nodes and edges
- [ ] Search returns relevant results

## 🤔 Common Questions

**Q: Why didn't I manually create nodes and edges?**
A: Graphiti uses LLMs to automatically extract entities and relationships from text. This is the magic of Graphiti - it understands content semantically!

**Q: How does Graphiti know what entities to extract?**
A: It uses your LLM (OpenAI by default) to analyze the text and identify important concepts and their relationships.

**Q: Can I see the raw Neo4j graph?**
A: Yes! Open Neo4j Browser and run `MATCH (n) RETURN n LIMIT 25` to see the raw graph structure.

## 🎯 Next Steps

Congratulations! You've successfully:

- ✅ Created your first Graphiti knowledge graph
- ✅ Added an episode with automatic entity extraction
- ✅ Performed semantic search on the graph
- ✅ Verified the system works end-to-end

**Ready for the next step?** Move on to **[02_episodes_and_entities](../02_episodes_and_entities/)** to dive deeper into how Graphiti extracts and organizes information.

## 📝 Key Takeaways

1. **Graphiti is automatic** - You provide text, it creates the knowledge graph
2. **Episodes are the input** - Text gets processed into structured knowledge
3. **Search is semantic** - Finds relevant information, not just keyword matches
4. **Everything is temporal** - Knowledge builds up over time
5. **LLMs do the work** - Automatic entity and relationship extraction

## 🎯 What You Learned

- How to install and configure Graphiti
- How to add episodes to create knowledge graphs
- How to search the graph to retrieve information
- The basic workflow: text → episodes → knowledge graph → search

---

**Congratulations!** You've successfully created your first temporal knowledge graph! 🎉

**Ready for the next step?** Continue to **[02_adding_episodes](../02_adding_episodes/)** to learn about different types of episodes and how they create different knowledge structures.

---

_"The best way to learn Graphiti is to start simple and build up your understanding step by step."_
