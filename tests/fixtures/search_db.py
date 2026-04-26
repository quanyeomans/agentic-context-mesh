"""sqlite-vec search index fixtures for tests.

This is the SEARCH INDEX only (BM25 + vectors). Not the entity store.
Entity tests use FakeNeo4jClient from neo4j_mock.py.
"""
import pytest
import sqlite3
import tempfile
from pathlib import Path

# 20 canonical test documents covering all intent categories
_TEST_DOCUMENTS = [
    # temporal
    {"path": "daily/2026-04-10.md", "title": "Daily Note 2026-04-10", "body": "Completed FEAT-081 implementation last week. Board updated."},
    {"path": "daily/2026-04-09.md", "title": "Daily Note 2026-04-09", "body": "Sprint review happened yesterday. Reviewed all open tickets."},
    # entity
    {"path": "entities/openclaw.md", "title": "OpenClaw", "body": "OpenClaw is an AI agent platform. It provides memory_search and tool orchestration."},
    {"path": "entities/acme-partners.md", "title": "Acme Partners", "body": "Acme Partners is a Microsoft services partner. Connected to OpenClaw via PARTNER_OF."},
    # procedural
    {"path": "runbooks/how-to-run-embed.md", "title": "How to run the embedding pipeline", "body": "Step 1: Run kairix embed. Step 2: Check recall. Step 3: Verify index."},
    {"path": "runbooks/how-to-restart.md", "title": "How to restart services", "body": "Run systemctl restart kairix. Verify with kairix onboard check."},
    # keyword
    {"path": "features/FEAT-081.md", "title": "FEAT-081", "body": "FEAT-081 implementation status: done. Merged in sprint 2A."},
    {"path": "features/FEAT-082.md", "title": "FEAT-082", "body": "FEAT-082 entity expansion. Target: sprint 2B."},
    # semantic
    {"path": "strategy/infra-cost.md", "title": "Infrastructure Cost Strategy", "body": "Infrastructure cost optimisation strategy: use spot instances, auto-scaling, and reserved capacity."},
    {"path": "strategy/tech-debt.md", "title": "Technical Debt Strategy", "body": "Approach to managing technical debt: quarterly reviews, sprint allocation, automated detection."},
    # multi_hop
    {"path": "research/openclaw-acme-partners-partnership.md", "title": "OpenClaw Acme Partners Partnership", "body": "The connection between OpenClaw and Acme Partners is a strategic partnership for enterprise AI deployment."},
    {"path": "research/entity-graph.md", "title": "Entity Graph Overview", "body": "Multi-hop relationships between organisations and people enable contextual search."},
    # extra semantic docs
    {"path": "notes/architecture.md", "title": "Architecture Overview", "body": "Kairix uses hybrid BM25 + vector search with RRF fusion for ranking."},
    {"path": "notes/performance.md", "title": "Performance Notes", "body": "NDCG@10 target is 0.78 overall. Temporal category requires temporal_boost."},
    {"path": "notes/sprint-2a.md", "title": "Sprint 2A Notes", "body": "Sprint 2A scope: S1-B, DEBT-1, TEST-1, EGQ-3, EGQ-4, FEAT-129, R3."},
    {"path": "notes/domains.md", "title": "Domain Model", "body": "Four bounded contexts: search, entity, embedding, briefing. Strict producer/consumer contracts."},
    {"path": "notes/testing.md", "title": "Test Strategy", "body": "Test pyramid: smoke, BDD, contract, integration, unit. pytest-bdd for acceptance tests."},
    {"path": "notes/neo4j.md", "title": "Neo4j Graph", "body": "Neo4j is the canonical entity store. Cypher queries via graph.client.cypher()."},
    {"path": "notes/embedding.md", "title": "Embedding Notes", "body": "Azure text-embedding-3-large produces 1536-dim float vectors stored in sqlite-vec."},
    {"path": "notes/curator.md", "title": "Curator Health", "body": "Curator health checks entity graph completeness: missing vault_path, stale entities, synthesis failures."},
]


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a tmp path for a SQLite DB."""
    return tmp_path / "test-kairix.db"


@pytest.fixture
def search_db(tmp_db_path):
    """Minimal in-memory SQLite DB with BM25 search table. No sqlite-vec extension required."""
    conn = sqlite3.connect(str(tmp_db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            agent TEXT DEFAULT 'shared',
            created_date TEXT,
            tokens INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(path, title, body, content='documents')")
    conn.commit()
    return conn


@pytest.fixture
def seeded_search_db(search_db):
    """Search DB seeded with 20 canonical test documents covering all intent categories."""
    for doc in _TEST_DOCUMENTS:
        search_db.execute(
            "INSERT OR REPLACE INTO documents (path, title, body) VALUES (?, ?, ?)",
            (doc["path"], doc["title"], doc["body"]),
        )
        search_db.execute(
            "INSERT INTO documents_fts (path, title, body) VALUES (?, ?, ?)",
            (doc["path"], doc["title"], doc["body"]),
        )
    search_db.commit()
    return search_db


# 30 representative reference library documents spanning all collections
_REFLIB_DOCUMENTS = [
    # agentic-ai (5)
    {"path": "agentic-ai/openai-cookbook/chain-of-thought.md", "title": "Chain of Thought Prompting", "body": "Chain-of-thought prompting improves reasoning by asking the model to show intermediate steps before the final answer."},
    {"path": "agentic-ai/dair-ai-prompts/few-shot.md", "title": "Few-Shot Prompting", "body": "Few-shot prompting provides examples of the desired input-output pattern before the actual query."},
    {"path": "agentic-ai/panaversity-agentic/multi-agent.md", "title": "Multi-Agent Systems", "body": "Multi-agent architectures coordinate multiple AI agents with different roles to solve complex problems collaboratively."},
    {"path": "agentic-ai/ms-gen-ai-beginners/rag.md", "title": "Retrieval-Augmented Generation", "body": "RAG combines information retrieval with text generation, grounding model output in retrieved documents."},
    {"path": "agentic-ai/autogen-docs/tool-use.md", "title": "Tool Use in Agents", "body": "Agents use tools to interact with external systems, execute code, search databases, and call APIs."},
    # engineering (5)
    {"path": "engineering/adr-examples/template.md", "title": "ADR Template", "body": "Architecture Decision Records document significant design choices with context, decision, and consequences."},
    {"path": "engineering/12factor/config.md", "title": "Config", "body": "Store config in the environment. An app's config varies between deploys but the code does not."},
    {"path": "engineering/soc-docs/incident-response.md", "title": "Incident Response Procedure", "body": "Step 1: Detect and triage. Step 2: Contain the threat. Step 3: Eradicate. Step 4: Recover. Step 5: Post-incident review."},
    {"path": "engineering/microsoft-code-with-playbook/testing.md", "title": "Testing Best Practices", "body": "Write tests first. Use the test pyramid: unit tests at the base, integration in the middle, end-to-end at the top."},
    {"path": "engineering/opentelemetry-docs/traces.md", "title": "Distributed Tracing", "body": "OpenTelemetry traces track requests across service boundaries using spans, trace IDs, and context propagation."},
    # data-and-analysis (4)
    {"path": "data-and-analysis/dbt-docs/models.md", "title": "dbt Models", "body": "dbt models are SQL select statements that transform raw data into analytics-ready tables."},
    {"path": "data-and-analysis/turing-way/reproducibility.md", "title": "Reproducible Research", "body": "Reproducibility requires version control, environment management, and documented workflows."},
    {"path": "data-and-analysis/mlops-guide/deployment.md", "title": "Model Deployment", "body": "Deploy models as REST APIs, batch pipelines, or embedded services. Monitor for drift and degradation."},
    {"path": "data-and-analysis/posthog-docs/experiments.md", "title": "Running Experiments", "body": "A/B tests require a hypothesis, control group, treatment group, and statistical significance threshold."},
    # philosophy (3)
    {"path": "philosophy/classical-western/meditations.md", "title": "Meditations", "body": "Marcus Aurelius wrote on impermanence, duty, and rational self-governance. Focus on what is within your control."},
    {"path": "philosophy/classical-eastern/tao-te-ching.md", "title": "Tao Te Ching", "body": "The Tao that can be told is not the eternal Tao. Effortless action and the way of water."},
    {"path": "philosophy/martial-arts-philosophy/bushido.md", "title": "Bushido", "body": "Bushido is the way of the warrior, combining martial arts with Confucian ethics, Buddhist calm, and Shinto loyalty."},
    # operating-models (2)
    {"path": "operating-models/cncf-platform-model/maturity.md", "title": "Platform Engineering Maturity", "body": "The CNCF maturity model defines four levels: Provisional, Operational, Scalable, and Optimising."},
    {"path": "operating-models/jph-ways-of-working/ground-rules.md", "title": "Team Ground Rules", "body": "Working agreements define how a team communicates, makes decisions, and resolves conflicts."},
    # product-and-design (2)
    {"path": "product-and-design/gong-practices/discovery.md", "title": "Product Discovery", "body": "Discovery validates customer problems before building solutions. Talk to users, prototype, test."},
    {"path": "product-and-design/usds-playbook/understand-needs.md", "title": "Understand What People Need", "body": "Start with user research. Observe people using existing services. Identify pain points."},
    # leadership (2)
    {"path": "leadership-and-culture/mozilla-open-leadership/participation.md", "title": "Open Participation", "body": "Open leadership invites contribution, shares decision-making, and builds trust through transparency."},
    {"path": "leadership-and-culture/jph-awesome-developing/feedback.md", "title": "Feedback Frameworks", "body": "Effective feedback is specific, timely, and focused on behaviour rather than personality."},
    # security (2)
    {"path": "security/cyclonedx-spec/sbom.md", "title": "Software Bill of Materials", "body": "An SBOM lists all components in a software product, enabling vulnerability tracking and supply chain security."},
    {"path": "security/openlane-grc/compliance.md", "title": "GRC Automation", "body": "Automate SOC 2, ISO 27001, and NIST 800-53 compliance with policy-as-code and continuous monitoring."},
    # economics (2)
    {"path": "economics-and-strategy/jph-business-model-canvas/canvas.md", "title": "Business Model Canvas", "body": "Nine building blocks: value propositions, customer segments, channels, relationships, revenue, resources, activities, partners, costs."},
    {"path": "economics-and-strategy/pymc-marketing/mmm.md", "title": "Marketing Mix Modelling", "body": "Bayesian marketing mix models measure the impact of marketing spend across channels on revenue."},
    # personal-effectiveness (1)
    {"path": "personal-effectiveness/jph-okrs/examples.md", "title": "OKR Examples", "body": "Objective: Improve customer retention. Key Result: Reduce churn from 5% to 3%. Key Result: NPS above 60."},
    # foundations (1)
    {"path": "foundations/neuromatch-compneuro/neural-data.md", "title": "Neural Data Analysis", "body": "Computational neuroscience uses statistical models to understand neural population coding and brain dynamics."},
    # health (1)
    {"path": "health-and-fitness/awesome-quantified-self/overview.md", "title": "Quantified Self", "body": "Track sleep, exercise, nutrition, mood, and biomarkers to make evidence-based health decisions."},
]


@pytest.fixture
def reflib_search_db(search_db):
    """Search DB seeded with 30 representative reference library documents."""
    for doc in _REFLIB_DOCUMENTS:
        search_db.execute(
            "INSERT INTO documents (hash, path, title) VALUES (?, ?, ?)",
            (doc["path"].replace("/", "_"), doc["path"], doc["title"]),
        )
        search_db.execute(
            "INSERT INTO content (hash, body) VALUES (?, ?)",
            (doc["path"].replace("/", "_"), doc["body"]),
        )
    search_db.commit()
    return search_db
