"""
Mock retrieval backend for the reference library contract benchmark suite.

Provides a deterministic, API-free retrieval fixture representing a curated
reference library. Instead of querying a live database, the mock returns results
from a 30-document in-process fixture corpus spanning the reference library
collections.

Design mirrors mock_retrieval.py: keyword matching against body text, scored by
overlap count, returned in score order. Registered as system name "mock-reflib".
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Fixture corpus — 30 representative reference library documents
# ---------------------------------------------------------------------------

FIXTURE_DOCUMENTS: list[dict] = [
    # --- agentic-ai (5) ---
    {
        "path": "reflib/agentic-ai/agent-loop-patterns.md",
        "title": "Agent Loop Patterns",
        "body": (
            "Agentic AI systems operate through observe-orient-decide-act loops. The core "
            "pattern involves an LLM receiving context from retrieval, reasoning about the "
            "task, selecting a tool or action, executing it, and feeding the result back "
            "into the next iteration. Loop termination requires explicit stop conditions "
            "to prevent runaway execution. Common patterns include ReAct (reason then act), "
            "plan-and-execute (decompose then dispatch), and reflexion (self-critique after "
            "each step). Production systems add timeout guards, token budget caps, and "
            "human-in-the-loop checkpoints to keep agents within operational bounds."
        ),
        "keywords": {
            "agent", "loop", "pattern", "observe", "orient", "decide", "act",
            "react", "plan", "execute", "reflexion", "agentic", "llm", "tool",
        },
    },
    {
        "path": "reflib/agentic-ai/tool-use-protocols.md",
        "title": "Tool Use Protocols",
        "body": (
            "Tool use in agentic systems follows a structured protocol: the model emits a "
            "tool call with a name and arguments, the runtime executes it in a sandbox, and "
            "the result is injected as an assistant message. OpenAI function calling and "
            "Anthropic tool_use share this pattern. Key design decisions include whether to "
            "allow parallel tool calls, how to handle errors (retry, skip, or escalate), "
            "and how to constrain the tool set per conversation turn. Rate limiting and "
            "cost attribution are essential for multi-tenant deployments."
        ),
        "keywords": {
            "tool", "use", "protocol", "function", "calling", "sandbox",
            "parallel", "error", "retry", "anthropic", "openai", "agent",
        },
    },
    {
        "path": "reflib/agentic-ai/memory-architectures.md",
        "title": "Memory Architectures",
        "body": (
            "Agent memory systems divide into working memory (current conversation context), "
            "episodic memory (past interaction logs), and semantic memory (structured knowledge "
            "store). Retrieval-augmented generation (RAG) bridges semantic memory and the LLM "
            "context window. Vector databases store embeddings for similarity search while "
            "graph databases capture entity relationships. Hybrid architectures combine BM25 "
            "lexical search with dense vector retrieval using reciprocal rank fusion to "
            "maximise recall across query types. Memory consolidation strategies periodically "
            "summarise episodic logs into durable semantic entries, preventing unbounded growth "
            "while preserving key facts. Effective memory systems balance freshness with "
            "relevance, ensuring recent context is weighted appropriately against long-term "
            "knowledge."
        ),
        "keywords": {
            "memory", "architecture", "working", "episodic", "semantic",
            "rag", "retrieval", "vector", "graph", "hybrid", "knowledge",
        },
    },
    {
        "path": "reflib/agentic-ai/prompt-engineering-patterns.md",
        "title": "Prompt Engineering Patterns",
        "body": (
            "Effective prompt engineering for agents combines system instructions, few-shot "
            "examples, and structured output formatting. Chain-of-thought prompting improves "
            "reasoning on complex tasks by eliciting intermediate steps. Constitutional AI "
            "patterns embed safety constraints directly in the system prompt. Template-based "
            "approaches use Jinja or Mustache to inject dynamic context (retrieved documents, "
            "user profile, tool results) into a stable prompt skeleton. Version-controlling "
            "prompts alongside code ensures reproducibility across deployments. Prompt "
            "compression techniques reduce token usage without sacrificing quality by "
            "distilling verbose instructions into concise directives. Evaluation of prompt "
            "effectiveness requires systematic A/B testing against held-out query sets with "
            "automated scoring."
        ),
        "keywords": {
            "prompt", "engineering", "pattern", "chain-of-thought", "few-shot",
            "system", "instruction", "template", "reasoning", "constitutional",
        },
    },
    {
        "path": "reflib/agentic-ai/evaluation-frameworks.md",
        "title": "Evaluation Frameworks",
        "body": (
            "Evaluating agentic systems requires multi-dimensional metrics: task completion "
            "rate, tool call accuracy, retrieval relevance (NDCG, MRR), and safety compliance. "
            "LLM-as-judge scales evaluation beyond manual annotation by having a strong model "
            "rate weaker model outputs. Benchmark suites should cover recall, conceptual, "
            "procedural, and entity query types with weighted category scores. Regression "
            "detection compares consecutive runs and flags score drops exceeding a threshold."
        ),
        "keywords": {
            "evaluation", "framework", "benchmark", "metric", "ndcg", "mrr",
            "judge", "regression", "score", "task", "completion", "accuracy",
        },
    },
    # --- engineering (5) ---
    {
        "path": "reflib/engineering/distributed-systems-fundamentals.md",
        "title": "Distributed Systems Fundamentals",
        "body": (
            "Distributed systems coordinate multiple networked processes to achieve a shared "
            "goal. The CAP theorem states that a distributed data store can provide at most "
            "two of consistency, availability, and partition tolerance simultaneously. "
            "Consensus protocols like Raft and Paxos enable leader election and log "
            "replication across nodes. Failure detection relies on heartbeat timeouts and "
            "phi-accrual detectors. Idempotency and exactly-once semantics are critical for "
            "reliable message processing in event-driven architectures."
        ),
        "keywords": {
            "distributed", "systems", "cap", "theorem", "consistency",
            "availability", "partition", "raft", "paxos", "consensus",
            "replication", "failure", "heartbeat",
        },
    },
    {
        "path": "reflib/engineering/observability-practice.md",
        "title": "Observability Practice",
        "body": (
            "Observability rests on three pillars: logs, metrics, and traces. Structured "
            "logging with JSON payloads enables machine parsing and correlation. Metrics "
            "use counters, gauges, and histograms exposed via Prometheus or OpenTelemetry. "
            "Distributed tracing propagates context IDs across service boundaries to "
            "reconstruct request flows. SLO-based alerting reduces noise by focusing on "
            "user-facing error budgets rather than raw threshold breaches. Dashboards "
            "should answer the four golden signals: latency, traffic, errors, and saturation."
        ),
        "keywords": {
            "observability", "logs", "metrics", "traces", "prometheus",
            "opentelemetry", "slo", "alerting", "latency", "dashboard",
            "structured", "logging", "tracing",
        },
    },
    {
        "path": "reflib/engineering/api-design-principles.md",
        "title": "API Design Principles",
        "body": (
            "Good API design prioritises consistency, discoverability, and backwards "
            "compatibility. RESTful APIs use resource-oriented URLs, standard HTTP verbs, "
            "and hypermedia links. GraphQL offers flexible querying but requires careful "
            "schema design to avoid N+1 problems. Versioning strategies include URL path "
            "versioning, header versioning, and content negotiation. Rate limiting protects "
            "backends while clear error responses with machine-readable codes and human-"
            "readable messages improve developer experience."
        ),
        "keywords": {
            "api", "design", "rest", "graphql", "versioning", "rate",
            "limiting", "http", "resource", "schema", "error", "backwards",
            "compatibility",
        },
    },
    {
        "path": "reflib/engineering/testing-strategies.md",
        "title": "Testing Strategies",
        "body": (
            "A balanced test portfolio includes unit tests for isolated logic, integration "
            "tests for component interactions, and end-to-end tests for critical user paths. "
            "The testing pyramid recommends many fast unit tests, fewer integration tests, and "
            "minimal E2E tests. Property-based testing with Hypothesis generates edge cases "
            "automatically. Contract testing with Pact verifies API compatibility between "
            "services. Mutation testing measures test suite effectiveness by injecting faults "
            "and checking whether tests detect them."
        ),
        "keywords": {
            "testing", "strategy", "unit", "integration", "end-to-end",
            "pyramid", "property", "hypothesis", "contract", "pact",
            "mutation", "test",
        },
    },
    {
        "path": "reflib/engineering/ci-cd-pipelines.md",
        "title": "CI CD Pipelines",
        "body": (
            "Continuous integration merges developer changes into a shared branch multiple "
            "times per day. Each merge triggers automated builds, linting, and test suites. "
            "Continuous delivery extends CI by automatically deploying passing builds to "
            "staging environments. Blue-green deployments and canary releases reduce rollout "
            "risk. Pipeline configuration lives in the repository as code (GitHub Actions, "
            "Azure Pipelines YAML). Secrets are injected at runtime from vaults, never "
            "committed to source control."
        ),
        "keywords": {
            "ci", "cd", "pipeline", "continuous", "integration", "delivery",
            "deploy", "canary", "blue-green", "github", "actions", "build",
            "automation",
        },
    },
    # --- data-and-analysis (4) ---
    {
        "path": "reflib/data-and-analysis/data-modelling-patterns.md",
        "title": "Data Modelling Patterns",
        "body": (
            "Data modelling translates business domains into structured schemas. Star schemas "
            "optimise analytical queries with fact and dimension tables. Graph models capture "
            "many-to-many relationships naturally. Document models (JSON, BSON) suit variable "
            "schemas and nested hierarchies. Normalisation reduces redundancy at the cost of "
            "join complexity; denormalisation trades storage for read performance. Schema "
            "evolution strategies (additive-only, migration scripts) keep production systems "
            "stable during iterative development."
        ),
        "keywords": {
            "data", "modelling", "schema", "star", "graph", "document",
            "normalisation", "denormalisation", "dimension", "fact", "model",
        },
    },
    {
        "path": "reflib/data-and-analysis/statistical-thinking.md",
        "title": "Statistical Thinking",
        "body": (
            "Statistical thinking applies probabilistic reasoning to data-driven decisions. "
            "Bayesian inference updates prior beliefs with observed evidence to produce "
            "posterior distributions. Frequentist hypothesis testing controls Type I error "
            "rates via p-values and confidence intervals. Effect size matters more than "
            "statistical significance for practical decisions. Simpson's paradox warns that "
            "aggregated trends can reverse when data is segmented. Power analysis determines "
            "required sample sizes before running experiments."
        ),
        "keywords": {
            "statistics", "statistical", "bayesian", "inference", "hypothesis",
            "testing", "p-value", "confidence", "effect", "size", "power",
            "analysis", "probability",
        },
    },
    {
        "path": "reflib/data-and-analysis/analytics-engineering.md",
        "title": "Analytics Engineering",
        "body": (
            "Analytics engineering sits between data engineering and data analysis. The dbt "
            "framework transforms raw data in the warehouse using SQL models with version "
            "control and testing. Metrics layers define business metrics once and expose them "
            "to multiple consumers. Data contracts between producers and consumers prevent "
            "breaking changes. Quality checks (row counts, null rates, schema drift) run "
            "automatically in the CI pipeline to catch issues before they reach dashboards."
        ),
        "keywords": {
            "analytics", "engineering", "dbt", "warehouse", "sql", "metrics",
            "data", "quality", "contract", "transform", "pipeline",
        },
    },
    {
        "path": "reflib/data-and-analysis/experiment-design.md",
        "title": "Experiment Design",
        "body": (
            "Rigorous experiment design controls for confounding variables and ensures "
            "reproducible results. A/B testing randomly assigns users to control and treatment "
            "groups. Multi-armed bandit approaches adaptively allocate traffic to winning "
            "variants. Pre-registration of hypotheses and analysis plans prevents p-hacking. "
            "Stratified sampling ensures subgroup representation. Feature flags decouple "
            "deployment from experiment activation, enabling safe incremental rollouts."
        ),
        "keywords": {
            "experiment", "design", "ab", "testing", "bandit", "control",
            "treatment", "hypothesis", "feature", "flag", "stratified",
            "rollout",
        },
    },
    # --- philosophy (3) ---
    {
        "path": "reflib/philosophy/epistemology-and-knowledge.md",
        "title": "Epistemology and Knowledge",
        "body": (
            "Epistemology studies the nature, sources, and limits of knowledge. Justified "
            "true belief was the classical definition until Gettier cases showed it was "
            "insufficient. Reliabilism grounds justification in the reliability of the "
            "cognitive process that produced the belief. Epistemic humility acknowledges "
            "the boundaries of what we can know. In information systems, these questions "
            "map to retrieval confidence: how certain are we that the retrieved document "
            "actually answers the user's question?"
        ),
        "keywords": {
            "epistemology", "knowledge", "belief", "justification", "gettier",
            "reliabilism", "epistemic", "humility", "truth", "certainty",
        },
    },
    {
        "path": "reflib/philosophy/ethics-of-automation.md",
        "title": "Ethics of Automation",
        "body": (
            "Automating decisions raises ethical questions about accountability, transparency, "
            "and fairness. When an AI agent acts on a user's behalf, who bears responsibility "
            "for errors — the developer, the deployer, or the user? Explainability requirements "
            "demand that automated decisions be auditable. Algorithmic fairness metrics (equal "
            "opportunity, demographic parity) detect bias in model outputs. The precautionary "
            "principle suggests constraining autonomous action until safety can be demonstrated."
        ),
        "keywords": {
            "ethics", "automation", "accountability", "transparency", "fairness",
            "bias", "explainability", "responsibility", "autonomous", "safety",
        },
    },
    {
        "path": "reflib/philosophy/systems-thinking.md",
        "title": "Systems Thinking",
        "body": (
            "Systems thinking analyses phenomena as interconnected wholes rather than isolated "
            "parts. Feedback loops (reinforcing and balancing) drive system behaviour over "
            "time. Leverage points are places where small interventions produce large effects. "
            "Emergence describes properties that arise from interactions but cannot be predicted "
            "from individual components alone. In software engineering, systems thinking helps "
            "diagnose cascading failures and identify root causes beyond the immediate symptom."
        ),
        "keywords": {
            "systems", "thinking", "feedback", "loop", "leverage", "emergence",
            "interconnected", "root", "cause", "cascade", "holistic",
        },
    },
    # --- operating-models (2) ---
    {
        "path": "reflib/operating-models/team-topologies.md",
        "title": "Team Topologies",
        "body": (
            "Team Topologies defines four fundamental team types: stream-aligned teams own "
            "end-to-end delivery of a value stream, enabling teams help others adopt new "
            "capabilities, complicated-subsystem teams manage deep specialist areas, and "
            "platform teams provide self-service internal products. Interaction modes "
            "(collaboration, X-as-a-service, facilitating) govern how teams work together. "
            "Conway's Law predicts that system architecture mirrors organisational structure, "
            "so intentional team design shapes software architecture."
        ),
        "keywords": {
            "team", "topologies", "stream-aligned", "platform", "enabling",
            "conway", "organisation", "structure", "interaction", "delivery",
        },
    },
    {
        "path": "reflib/operating-models/okr-framework.md",
        "title": "OKR Framework",
        "body": (
            "Objectives and Key Results (OKRs) align organisational effort by setting "
            "ambitious objectives with measurable key results. Objectives are qualitative "
            "and inspirational; key results are quantitative and time-bound. Cadence is "
            "typically quarterly with weekly check-ins. Stretch targets (70% completion as "
            "success) encourage ambition without penalising partial achievement. OKRs cascade "
            "from company level to team level but should not be used as individual performance "
            "metrics to avoid sandbagging."
        ),
        "keywords": {
            "okr", "objectives", "key", "results", "goals", "alignment",
            "quarterly", "measurable", "stretch", "target", "framework",
        },
    },
    # --- product-and-design (2) ---
    {
        "path": "reflib/product-and-design/jobs-to-be-done.md",
        "title": "Jobs to Be Done",
        "body": (
            "The Jobs-to-Be-Done framework reframes product development around the progress "
            "customers are trying to make. A job is a stable unit of demand that exists "
            "independently of any particular solution. Customers hire products to get jobs "
            "done; they fire products that don't deliver. Job stories follow the format: "
            "'When [situation], I want to [motivation], so I can [outcome]'. Mapping the "
            "job's functional, emotional, and social dimensions reveals unmet needs that "
            "features alone cannot address."
        ),
        "keywords": {
            "jobs", "done", "jtbd", "product", "customer", "demand",
            "progress", "outcome", "functional", "emotional", "hire",
        },
    },
    {
        "path": "reflib/product-and-design/design-system-principles.md",
        "title": "Design System Principles",
        "body": (
            "A design system provides reusable components and guidelines that ensure visual "
            "and interaction consistency across products. Atomic design organises components "
            "into atoms, molecules, organisms, templates, and pages. Design tokens encode "
            "colour, spacing, and typography as platform-agnostic variables. Accessibility "
            "standards (WCAG AA) must be baked into component defaults, not added as an "
            "afterthought. Versioning the design system alongside the codebase keeps "
            "designers and developers in sync."
        ),
        "keywords": {
            "design", "system", "component", "atomic", "token", "accessibility",
            "wcag", "consistency", "typography", "colour", "reusable",
        },
    },
    # --- leadership (2) ---
    {
        "path": "reflib/leadership/psychological-safety.md",
        "title": "Psychological Safety",
        "body": (
            "Psychological safety is the shared belief that the team is safe for interpersonal "
            "risk-taking. Google's Project Aristotle identified it as the strongest predictor "
            "of team effectiveness. Leaders foster it by modelling vulnerability, responding "
            "constructively to mistakes, and explicitly inviting dissent. Without psychological "
            "safety, team members withhold ideas, hide errors, and avoid experimentation — "
            "all of which degrade innovation and learning velocity."
        ),
        "keywords": {
            "psychological", "safety", "team", "trust", "vulnerability",
            "aristotle", "effectiveness", "leadership", "dissent", "risk",
        },
    },
    {
        "path": "reflib/leadership/decision-making-frameworks.md",
        "title": "Decision Making Frameworks",
        "body": (
            "Structured decision-making reduces cognitive bias and improves consistency. The "
            "DACI model assigns a Driver, Approver, Contributors, and Informed parties to "
            "each decision. Pre-mortem analysis imagines a future failure and works backward "
            "to identify risks. The Cynefin framework classifies situations as simple, "
            "complicated, complex, or chaotic, prescribing different decision approaches "
            "for each domain. Reversible decisions should be made quickly; irreversible "
            "ones deserve deliberation."
        ),
        "keywords": {
            "decision", "making", "framework", "daci", "pre-mortem", "cynefin",
            "bias", "cognitive", "reversible", "classification", "leadership",
        },
    },
    # --- security (2) ---
    {
        "path": "reflib/security/zero-trust-architecture.md",
        "title": "Zero Trust Architecture",
        "body": (
            "Zero trust assumes no implicit trust based on network location. Every request "
            "is authenticated, authorised, and encrypted regardless of origin. Identity is "
            "the new perimeter: users and services authenticate via short-lived tokens issued "
            "by an identity provider. Micro-segmentation limits lateral movement by enforcing "
            "least-privilege access at the workload level. Continuous verification replaces "
            "one-time login; device posture and behavioural signals feed adaptive access "
            "policies."
        ),
        "keywords": {
            "zero", "trust", "architecture", "security", "authentication",
            "authorisation", "identity", "token", "micro-segmentation",
            "least-privilege", "encrypted",
        },
    },
    {
        "path": "reflib/security/secrets-management.md",
        "title": "Secrets Management",
        "body": (
            "Secrets management ensures that API keys, certificates, and database credentials "
            "are never stored in source code or configuration files. Vault systems (HashiCorp "
            "Vault, Azure Key Vault, AWS Secrets Manager) provide centralised storage with "
            "audit logging and automatic rotation. Applications retrieve secrets at runtime "
            "via sidecar injectors or environment variable substitution. Rotation policies "
            "limit the blast radius of leaked credentials by ensuring short-lived validity."
        ),
        "keywords": {
            "secrets", "management", "vault", "key", "credential", "rotation",
            "api", "certificate", "hashicorp", "azure", "audit", "security",
        },
    },
    # --- economics (2) ---
    {
        "path": "reflib/economics/incentive-design.md",
        "title": "Incentive Design",
        "body": (
            "Incentive design applies economic principles to align individual behaviour with "
            "organisational goals. Principal-agent theory models the tension between a "
            "delegator and an executor with different information and motivations. Moral "
            "hazard arises when one party takes risks because another bears the cost. "
            "Mechanism design (reverse game theory) constructs rules that lead self-interested "
            "actors to collectively optimal outcomes. In platform economics, two-sided "
            "incentives must balance supply and demand participants."
        ),
        "keywords": {
            "incentive", "design", "economics", "principal", "agent", "moral",
            "hazard", "mechanism", "game", "theory", "platform", "behaviour",
        },
    },
    {
        "path": "reflib/economics/cost-of-complexity.md",
        "title": "Cost of Complexity",
        "body": (
            "Complexity imposes hidden costs on organisations: coordination overhead, cognitive "
            "load, and slower decision-making. Brooks's Law warns that adding people to a late "
            "project makes it later because communication paths grow quadratically. Technical "
            "debt compounds like financial debt — deferred simplification increases future "
            "change cost. Simplification audits identify accidental complexity that can be "
            "removed without reducing capability. The goal is essential complexity only."
        ),
        "keywords": {
            "complexity", "cost", "brooks", "law", "technical", "debt",
            "cognitive", "load", "simplification", "coordination", "overhead",
        },
    },
    # --- personal-effectiveness (1) ---
    {
        "path": "reflib/personal-effectiveness/deep-work-practices.md",
        "title": "Deep Work Practices",
        "body": (
            "Deep work is the ability to focus without distraction on cognitively demanding "
            "tasks. Cal Newport distinguishes deep work from shallow work (logistical, "
            "easily replicated tasks). Strategies include time-blocking dedicated focus "
            "sessions, batching communication into fixed windows, and using shutdown rituals "
            "to transition out of work mode. The residue of attention from context-switching "
            "degrades performance for up to 20 minutes. Protecting deep work time is a "
            "competitive advantage for knowledge workers."
        ),
        "keywords": {
            "deep", "work", "focus", "distraction", "newport", "time-blocking",
            "attention", "shallow", "productivity", "concentration",
        },
    },
    # --- foundations (1) ---
    {
        "path": "reflib/foundations/first-principles-reasoning.md",
        "title": "First Principles Reasoning",
        "body": (
            "First principles reasoning breaks problems down to their most fundamental truths "
            "and builds up from there, rather than reasoning by analogy. Aristotle defined a "
            "first principle as the first basis from which a thing is known. In engineering, "
            "this means questioning assumptions: why does the system work this way? What "
            "constraints are real versus inherited from legacy decisions? Elon Musk famously "
            "applied first principles to rocketry costs, questioning why materials cost a "
            "fraction of the finished rocket."
        ),
        "keywords": {
            "first", "principles", "reasoning", "fundamental", "analogy",
            "assumptions", "aristotle", "basis", "truth", "engineering",
        },
    },
    # --- health (1) ---
    {
        "path": "reflib/health/cognitive-load-management.md",
        "title": "Cognitive Load Management",
        "body": (
            "Cognitive load theory describes the mental effort required to process information. "
            "Intrinsic load comes from task complexity; extraneous load from poor presentation "
            "or tooling; germane load from the effort of building mental models. Reducing "
            "extraneous load (clear interfaces, consistent conventions) frees capacity for "
            "germane processing. For developers, cognitive load management means limiting "
            "the number of systems a person must hold in working memory simultaneously. "
            "Team Topologies applies this principle to organisational design."
        ),
        "keywords": {
            "cognitive", "load", "management", "intrinsic", "extraneous",
            "germane", "mental", "effort", "working", "memory", "capacity",
        },
    },
]

# Build keyword -> document index for O(k) lookup per query
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


def mock_reflib_retrieve(query: str, limit: int = 10) -> tuple[list[str], list[str], dict]:
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
    snippets = [FIXTURE_DOCUMENTS[i]["body"][:500] for i, _ in top]
    meta = {
        "system": "mock-reflib",
        "n_matched": len(top),
        "query_tokens": sorted(query_tokens),
    }
    return paths, snippets, meta
