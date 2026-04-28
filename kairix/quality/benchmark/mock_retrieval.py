"""
Mock retrieval backend for the contract benchmark suite.

Provides a deterministic, API-free retrieval fixture for CI benchmark runs.
Instead of querying kairix, the mock returns results from a small in-process
fixture corpus whose keyword-to-document mappings are committed to the repo.

Design:
  - Fixture documents are plain dicts: {path, title, snippet, keywords}
  - For a given query, each fixture document is scored by how many of its keywords
    appear in the query (case-insensitive, whole-word). Documents are returned in
    score order.
  - This is intentionally simple — it tests the scoring/fusion/boosting CODE paths,
    not the retrieval quality. The CI gate catches regressions in pipeline logic.

The fixture corpus is defined below. It is small (~20 documents) and stable.
Do not change fixture paths once the baseline is committed — path changes will
shift the baseline score and require a new baseline run.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Fixture corpus
# ---------------------------------------------------------------------------

# Each fixture: (path, title, snippet, keywords_set)
# path: relative document path (used as the "retrieved" path by the runner)
# keywords: set of terms that will cause this document to be returned for a query
FIXTURE_DOCUMENTS: list[dict] = [
    {
        "path": "concept/retrieval-architecture.md",
        "title": "Retrieval Architecture",
        "snippet": "Hybrid search combines BM25 lexical matching with dense vector search via RRF fusion.",
        "keywords": {"retrieval", "architecture", "hybrid", "search", "bm25", "vector", "rrf"},
    },
    {
        "path": "runbooks/deploy-checklist.md",
        "title": "Deploy Checklist",
        "snippet": "Deployment checklist: verify embeddings, check Neo4j connectivity, confirm secret rotation.",
        "keywords": {"deploy", "deployment", "checklist", "runbook", "procedure", "steps", "production"},
    },
    {
        "path": "person/alice-engineer.md",
        "title": "Alice Engineer",
        "snippet": "Alice is a senior software engineer who leads the retrieval infrastructure team.",
        "keywords": {"alice", "engineer", "software", "person", "entity", "infrastructure"},
    },
    {
        "path": "notes/2026-03-15-standup.md",
        "title": "2026-03-15 Standup",
        "snippet": "Standup notes from 2026-03-15: search API deployed, entity graph seeded.",
        "keywords": {"2026-03-15", "march", "standup", "meeting", "notes", "daily"},
    },
    {
        "path": "concept/entity-graph.md",
        "title": "Entity Graph",
        "snippet": "The Neo4j entity graph stores person, organisation, and concept nodes with MENTIONS edges.",
        "keywords": {"entity", "graph", "neo4j", "relationship", "knowledge", "nodes", "mentions"},
    },
    {
        "path": "how-to/how-to-setup-kairix.md",
        "title": "How to Setup Kairix",
        "snippet": "Step-by-step guide to installing and configuring kairix for a new vault.",
        "keywords": {"setup", "kairix", "install", "configure", "guide", "how-to", "quickstart"},
    },
    {
        "path": "concept/rrf-fusion.md",
        "title": "RRF Fusion",
        "snippet": "Reciprocal Rank Fusion (RRF) combines multiple ranked lists into a single merged ranking.",
        "keywords": {"rrf", "fusion", "rank", "reciprocal", "combination", "merge", "ranking"},
    },
    {
        "path": "project/q2-planning.md",
        "title": "Q2 Planning",
        "snippet": "Q2 roadmap: cross-encoder re-ranking, file watcher, REST API, observability dashboard.",
        "keywords": {"planning", "project", "q2", "roadmap", "goals", "priorities", "sprint"},
    },
    {
        "path": "concept/temporal-indexing.md",
        "title": "Temporal Indexing",
        "snippet": "Temporal indexing extracts dates from vault notes and builds a date-keyed chunk index.",
        "keywords": {"temporal", "index", "date", "chunk", "time", "chronological", "dates"},
    },
    {
        "path": "person/bob-manager.md",
        "title": "Bob Manager",
        "snippet": "Bob leads the product team and coordinates stakeholder reviews for quarterly releases.",
        "keywords": {"bob", "manager", "person", "entity", "leadership", "product", "stakeholder"},
    },
    {
        "path": "concept/bm25-scoring.md",
        "title": "BM25 Scoring",
        "snippet": "BM25 (Best Match 25) is a probabilistic term-frequency ranking function used in full-text search.",
        "keywords": {"bm25", "scoring", "term", "frequency", "full-text", "probabilistic", "okapi"},
    },
    {
        "path": "runbooks/runbook-embed-failure.md",
        "title": "Runbook Embed Failure",
        "snippet": "Diagnose and recover from vector embedding failures: check secrets, retry with --force.",
        "keywords": {"embed", "embedding", "failure", "runbook", "recover", "diagnose", "vector"},
    },
    {
        "path": "concept/multi-hop-queries.md",
        "title": "Multi-Hop Query Planning",
        "snippet": "Multi-hop queries require decomposing into sub-queries: entity lookup then document retrieval.",
        "keywords": {"multi-hop", "multi", "hop", "query", "planning", "decompose", "sub-query"},
    },
    {
        "path": "concept/semantic-search.md",
        "title": "Semantic Search",
        "snippet": "Semantic search uses dense vector embeddings to find conceptually similar documents.",
        "keywords": {"semantic", "search", "dense", "embedding", "conceptual", "similarity", "meaning"},
    },
    {
        "path": "organisation/acme-corp.md",
        "title": "Acme Corp",
        "snippet": "Acme Corp is a technology consulting firm and early adopter of the kairix retrieval platform.",
        "keywords": {"acme", "acme-corp", "organisation", "organization", "consulting", "firm", "client"},
    },
    {
        "path": "procedure/onboarding-checklist.md",
        "title": "Onboarding Checklist",
        "snippet": "Onboarding procedure: vault access, Neo4j connection, embedding config, search validation.",
        "keywords": {"onboarding", "procedure", "sop", "checklist", "new", "start", "begin"},
    },
    {
        "path": "concept/token-budget.md",
        "title": "Token Budget",
        "snippet": "The token budget allocator assigns retrieved chunks to L0/L1/L2 tiers based on score and size.",
        "keywords": {"token", "budget", "tier", "allocator", "context", "window", "l0", "l1"},
    },
    {
        "path": "notes/2026-04-10-review.md",
        "title": "2026-04-10 Review",
        "snippet": "Quarterly review on 2026-04-10: search API performance, entity graph health, benchmark results.",
        "keywords": {"2026-04-10", "april", "review", "quarterly", "performance", "results"},
    },
    {
        "path": "concept/vector-embeddings.md",
        "title": "Vector Embeddings",
        "snippet": (
            "Vector embeddings encode text as dense float32 arrays. kairix uses 1536-dim Azure OpenAI embeddings."
        ),
        "keywords": {"vector", "embeddings", "dense", "float", "azure", "dimensions", "encode"},
    },
    {
        "path": "how-to/how-to-debug-search.md",
        "title": "How to Debug Search",
        "snippet": "Debug search ranking: compare BM25 vs vector scores, check entity boost, inspect RRF output.",
        "keywords": {"debug", "search", "ranking", "troubleshoot", "diagnose", "inspect", "how-to"},
    },
]

# Build keyword → document index for O(k) lookup per query
_KEYWORD_INDEX: dict[str, list[int]] = {}
for _i, _doc in enumerate(FIXTURE_DOCUMENTS):
    for _kw in _doc["keywords"]:
        _KEYWORD_INDEX.setdefault(_kw.lower(), []).append(_i)


# ---------------------------------------------------------------------------
# Mock retriever
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> set[str]:
    """Extract lowercase word tokens from text."""
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()))


def mock_retrieve(query: str, limit: int = 10) -> tuple[list[str], list[str], dict]:
    """
    Return fixture documents whose keywords overlap with the query.

    Scoring: count of matching keywords. Ties broken by fixture index order.
    Returns (paths, snippets, metadata) matching the _retrieve() contract in runner.py.
    """
    query_tokens = _tokenise(query)
    scores: dict[int, int] = {}

    for token in query_tokens:
        for doc_idx in _KEYWORD_INDEX.get(token, []):
            scores[doc_idx] = scores.get(doc_idx, 0) + 1

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = ranked[:limit]

    paths = [FIXTURE_DOCUMENTS[i]["path"] for i, _ in top]
    snippets = [FIXTURE_DOCUMENTS[i]["snippet"] for i, _ in top]
    meta = {
        "system": "mock",
        "n_matched": len(top),
        "query_tokens": sorted(query_tokens),
    }
    return paths, snippets, meta
