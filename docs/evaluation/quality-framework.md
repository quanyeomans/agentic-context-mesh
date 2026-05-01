# Kairix Quality Framework

A structured approach to evaluating and maintaining the quality of the kairix platform across security, engineering, testing, operations, and non-functional requirements. Use this framework for pre-release audits, regular reviews, and CI gate definitions.

## How to use this document

1. **Pre-release audit**: Run all sections before merging develop → main
2. **Regular review**: Run monthly or after major feature sprints
3. **CI integration**: Automate the checks marked [automatable] in your pipeline
4. **Roadmap input**: Items scored "needs work" feed into the engineering backlog

Each section has a maturity scale:
- **Strong** — meets or exceeds expectations for a public open-source release
- **Adequate** — functional but has known gaps that should be addressed
- **Needs work** — blocks release or creates material risk

---

## 1. Security Posture

### 1.1 Input Validation
- [ ] All user-facing entry points sanitise input before use (CLI args, MCP tool params, config values)
- [ ] FTS5 MATCH queries use parameterised inputs, not string interpolation
- [ ] Neo4j Cypher queries use parameter binding or validated enums for labels
- [ ] File paths are validated against allowed roots (no path traversal)
- [ ] Config values are range-checked at load time

### 1.2 Secret Management
- [ ] No secrets hardcoded in source code [automatable: `detect-secrets`]
- [ ] Secrets resolved via env → file → Key Vault (never logged)
- [ ] Error messages do not leak secret values, internal paths, or connection strings
- [ ] Lockfiles and temp files are in user-owned directories, not world-writable /tmp

### 1.3 Network Security
- [ ] MCP SSE transport binds to localhost by default
- [ ] TLS verification is explicit (not suppressed via `# nosec`)
- [ ] No unauthenticated write endpoints exposed
- [ ] Dependency versions have upper bounds to prevent supply chain drift [automatable: `pip-audit`]

### 1.4 Data Residency
- [ ] Document content is only sent to configured LLM endpoint for embedding
- [ ] No telemetry, analytics, or external calls without explicit opt-in
- [ ] Query logs are disabled by default and documented as privacy-sensitive
- [ ] All index/vector/graph data stored locally (SQLite + Neo4j on user infrastructure)

---

## 2. Engineering Quality (DRY / SOLID / Clean Code)

### 2.1 DRY (Don't Repeat Yourself)
- [ ] Constants defined once and imported (CATEGORY_WEIGHTS, CATEGORY_ALIASES)
- [ ] Metric functions (NDCG, Hit@k, MRR) in one module, imported everywhere
- [ ] Path canonicalisation in one function, called from all fusion modes
- [ ] No duplicated stop-word lists, query normalisation, or DB connection patterns

### 2.2 SOLID Principles
- [ ] **Single Responsibility**: No function >200 lines; no module mixing concerns
- [ ] **Open/Closed**: New intents, boosts, or fusion strategies addable without modifying existing code
- [ ] **Liskov Substitution**: All Protocol implementations are substitutable (no NotImplementedError stubs)
- [ ] **Interface Segregation**: Protocols are focused (EmbedProvider, LLMBackend, EntityResolver)
- [ ] **Dependency Inversion**: Modules depend on protocols, not concrete classes

### 2.3 Code Smells
- [ ] No magic numbers — all thresholds and constants are named
- [ ] No commented-out code or dead branches (`if True:`, unused imports)
- [ ] No dynamic attribute assignment (`setattr` on dataclasses)
- [ ] Module-level state is minimised and clearly documented
- [ ] Lazy imports are used only for circular dependency avoidance, not as a pattern

### 2.4 API Surface
- [ ] `__init__.py` exports public types (SearchResult, RetrievalConfig, QueryIntent)
- [ ] Internal modules use `_` prefix convention
- [ ] CLI signatures are consistent across all subcommands
- [ ] MCP tool docstrings follow plain-language standard (grade 8 reading level)

---

## 3. Test Strategy

### 3.1 Test Pyramid
- [ ] Unit tests: >80% of test suite, fast (<30s total), no I/O [automatable]
- [ ] Integration tests: real SQLite + FTS5, mock LLM, verify pipeline end-to-end
- [ ] Contract tests: all Protocol interfaces have conformance checks
- [ ] E2E tests: gated behind env var, test real search pipeline
- [ ] BDD: feature files for key user-facing behaviours

### 3.2 Coverage
- [ ] Every public module has corresponding tests
- [ ] Critical path (search → fusion → boost → budget) tested end-to-end
- [ ] Error paths tested (DB unavailable, LLM timeout, Neo4j down)
- [ ] Edge cases: empty queries, single-token queries, very long queries
- [ ] No dead test files or tests that always pass regardless of code changes

### 3.3 Test Quality
- [ ] Tests assert behaviour, not implementation (mock at boundaries, not internals)
- [ ] Shared fixtures in conftest.py, not duplicated per file
- [ ] All tests carry a marker (@pytest.mark.unit, integration, etc.)
- [ ] Test names describe the scenario, not the function being tested

### 3.4 Benchmark Quality Gates
- [ ] NDCG@10 threshold defined per phase (0.750 for Phase 3)
- [ ] No single-category regression >0.05 accepted without explicit decision
- [ ] Independent gold suite methodology (TREC pooling + LLM judge)
- [ ] Category alias mapping verified (semantic→recall, keyword→conceptual)

---

## 4. Operational Observability

### 4.1 Logging
- [ ] Search events logged to structured JSONL (query hash, intent, latency, result count)
- [ ] Embed events logged with chunk count, duration, errors
- [ ] Error events include enough context to diagnose without reproducing
- [ ] No sensitive data in log output (query text gated behind KAIRIX_LOG_QUERIES)

### 4.2 Health Checks
- [ ] `kairix onboard check` covers all critical paths (9 checks)
- [ ] Each check degrades gracefully (returns status, doesn't crash)
- [ ] Fix hints are actionable and deployment-aware
- [ ] Health check runnable from Docker, systemd, and bare-metal

### 4.3 OpenTelemetry Readiness
- [ ] Key operations have span-level timing (search, embed, entity boost)
- [ ] Error rates trackable per operation type
- [ ] Token spend per query estimable from logs
- [ ] OTEL collector configuration documented for common backends

### 4.4 Performance Monitoring
- [ ] Search latency tracked per query (p50, p95, p99 derivable from logs)
- [ ] Embed throughput tracked (chunks/second, API calls/minute)
- [ ] Vector search failure rate tracked (vec_failed flag in search results)
- [ ] Index freshness trackable (last embed timestamp in logs)

---

## 5. Non-Functional Requirements

### 5.1 Performance
- [ ] Search latency <500ms p95 for standard queries
- [ ] Embed throughput: ≥100 chunks/minute on standard API tier
- [ ] Memory usage: <2GB for 10,000-document index
- [ ] Startup time: <5s to first query readiness

### 5.2 Scalability
- [ ] SQLite handles up to ~50,000 documents without degradation
- [ ] FTS5 index size scales linearly with document count
- [ ] Vector search scales with usearch HNSW constraints documented
- [ ] Neo4j graph size limits documented (community edition: no clustering)

### 5.3 Reliability
- [ ] Search never raises — returns empty results with error field
- [ ] Embed retries with exponential backoff (SDK-based)
- [ ] Vector search failure falls back to BM25-only
- [ ] Neo4j unavailability degrades gracefully (entity boost disabled)
- [ ] No data corruption on concurrent access (WAL mode, advisory locks)

### 5.4 Search Quality Drift
- [ ] Benchmark suite versioned alongside code (gold paths stable across releases)
- [ ] NDCG@10 tracked per release in CHANGELOG
- [ ] Category-level regression detection in `kairix eval monitor`
- [ ] Independent gold suite rebuilt periodically (not tied to any search config)

---

## 6. Responsible AI / Commercial Readiness

### 6.1 Transparency
- [ ] Search results include source paths (attributable)
- [ ] LLM calls are identifiable in logs (model, deployment, purpose)
- [ ] No hidden LLM calls in the search path (only in research/synthesis/eval)
- [ ] Benchmark methodology published and reproducible

### 6.2 Data Governance
- [ ] Document content never stored by kairix (only indexed derivatives)
- [ ] PII handling documented (entity stubs contain names by design)
- [ ] Data retention policy configurable (log rotation, index rebuild)
- [ ] GDPR-relevant: user can delete their index and all derived data

### 6.3 Licensing
- [ ] Apache 2.0 for kairix package (patent grant, commercial-friendly)
- [ ] Neo4j Community GPL3 dependency documented (not bundled, Bolt protocol)
- [ ] All pip dependencies have compatible licences
- [ ] No proprietary model weights or training data in the package

### 6.4 Commercial Deployment
- [ ] Docker Compose packaging for self-hosted deployment
- [ ] Configuration wizard for first-time setup
- [ ] Ontology templates for common use cases
- [ ] MCP integration documentation for major agent platforms
- [ ] Cost estimation guidance ($25/month reference deployment)

---

## Scoring Template

Use this table when running an audit:

| Area | Score | Key Finding | Action |
|------|-------|-------------|--------|
| 1.1 Input Validation | Strong / Adequate / Needs work | | |
| 1.2 Secret Management | | | |
| 1.3 Network Security | | | |
| 1.4 Data Residency | | | |
| 2.1 DRY | | | |
| 2.2 SOLID | | | |
| 2.3 Code Smells | | | |
| 2.4 API Surface | | | |
| 3.1 Test Pyramid | | | |
| 3.2 Coverage | | | |
| 3.3 Test Quality | | | |
| 3.4 Benchmark Gates | | | |
| 4.1 Logging | | | |
| 4.2 Health Checks | | | |
| 4.3 OTEL Readiness | | | |
| 4.4 Performance Monitoring | | | |
| 5.1 Performance | | | |
| 5.2 Scalability | | | |
| 5.3 Reliability | | | |
| 5.4 Quality Drift | | | |
| 6.1 Transparency | | | |
| 6.2 Data Governance | | | |
| 6.3 Licensing | | | |
| 6.4 Commercial Deployment | | | |
