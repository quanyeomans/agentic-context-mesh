# Canonical behaviour spec for the unified kairix benchmarking suite.
#
# This feature is the contract that the unified `kairix benchmark run`
# CLI is delivered against. Implementation phases P1-P8 of the unified
# benchmarking initiative wire the step skeletons in
# `tests/bdd/steps/benchmark_unified_contract_steps.py` and add the
# `tests/bdd/test_benchmark_unified_contract.py` loader. Until those
# land, this file is dormant: it documents intent and serves as the
# regression gate that future phases must light up.
#
# Lenses covered:
#
#   * Quantitative retrieval ranking (NDCG / Hit / MRR) by query type
#   * Qualitative answer correctness (LLM judge) by query type
#   * Combined retrieval + synthesis consistency
#   * Per-query latency, sustained throughput, error rate
#   * Soak stability — memory growth, fd leakage, determinism drift,
#     log volume growth
#   * Scope / agent / collection RBAC filtering
#   * Gate-failure exit codes for CI consumption
#   * Focus-area segmentation
#
# Floors in the Examples tables sit just below the observed v2026.5.17
# values reported in docs/evaluation/EVALUATION.md (Keyword 0.775,
# Entity 0.8626, Procedural 0.8716, Temporal 0.7930, Multi-hop 0.721,
# Semantic 0.842). They are floors, not targets — exceeding them is
# the normal state; falling below them is a regression.

Feature: Unified kairix benchmarking suite — quality, performance, stability under scope and agent constraints
  As an operator releasing kairix
  I want a single benchmarking suite that validates retrieval and synthesis
  quality (quantitatively and qualitatively), performance under load, and
  stability across repeated workloads — segmented by collection, agent,
  RBAC scope, and focus area
  So that I can catch regressions that impact usability and agent-experienced
  flakiness before they ship.

  Background:
    Given a benchmark suite that declares queries, collections, scope, agent context, mode, and gates
    And a corpus ingested through the operator-facing ingest flow

  # --- Quantitative retrieval ranking -------------------------------------

  @happy_path
  Scenario Outline: Quantitative retrieval ranking by query type
    Given a query of type "<query_type>" carrying gold-titled relevant documents
    When the operator runs the benchmark in single-shot mode
    Then NDCG at 10 is at least <ndcg_floor>
    And Hit at 5 is at least <hit_floor>
    And MRR at 10 is at least <mrr_floor>

    Examples: Retrieval floors by query type
      | query_type | ndcg_floor | hit_floor | mrr_floor |
      | keyword    | 0.75       | 0.90      | 0.70      |
      | entity     | 0.80       | 0.95      | 0.75      |
      | procedural | 0.85       | 0.95      | 0.80      |
      | temporal   | 0.75       | 0.90      | 0.65      |
      | multi-hop  | 0.70       | 0.85      | 0.55      |
      | semantic   | 0.80       | 0.95      | 0.70      |

  # --- Qualitative answer correctness via LLM judge -----------------------

  Scenario Outline: Qualitative answer correctness via LLM judge
    Given a query of type "<query_type>" carrying an expected answer
    When the operator runs the benchmark in single-shot mode
    Then the LLM judge scores the synthesised answer at least <judge_floor>

    Examples: Judge floors by query type
      | query_type                   | judge_floor |
      | keyword                      | 0.70        |
      | entity                       | 0.70        |
      | procedural                   | 0.75        |
      | temporal                     | 0.60        |
      | multi-hop                    | 0.50        |
      | semantic                     | 0.65        |
      | conversational-multi-session | 0.50        |

  # --- Combined retrieval + synthesis -------------------------------------

  Scenario: Combined retrieval and synthesis consistency
    Given a query carrying both gold-titled documents and an expected answer
    When the operator runs the benchmark in single-shot mode
    Then NDCG at 10 meets its floor for the query type
    And the LLM judge score meets its floor for the query type
    And the top-ranked documents materially contribute to the synthesised answer

  # --- Performance lenses -------------------------------------------------

  Scenario: Per-query latency under cold and warm conditions
    Given a benchmark suite with latency gates declared
    When the operator runs the benchmark in single-shot mode
    Then p50 latency is below the cold gate
    And p95 latency is below the warm gate
    And p99 latency is below the tail gate

  Scenario: Sustained throughput under concurrent load
    Given a benchmark suite with concurrency 32 and duration 60 seconds
    When the operator runs the benchmark in concurrent mode
    Then sustained queries per second meet the throughput floor
    And p95 latency under load remains below its gate
    And the error rate remains below its ceiling

  # --- Stability under soak ----------------------------------------------

  Scenario: No resource growth across repeated workloads
    Given a soak suite that repeats the workload 100 times
    When the operator runs the benchmark in soak mode
    Then per-iteration memory growth stays under its gate
    And no file descriptors leak between iterations
    And determinism drift between runs stays under its gate
    And per-iteration log volume growth stays under its gate

  # --- Scope / agent / collection RBAC ------------------------------------

  Scenario Outline: Scope and collection filtering respect RBAC boundaries
    Given an agent "<agent>" with scope "<scope>" on collection "<collection>"
    When the operator runs the benchmark for that agent
    Then the results contain only documents the agent is authorised to see
    And cross-collection and cross-agent leakage produces zero hits

    Examples: RBAC matrix
      | agent       | scope         | collection      |
      | agent-alpha | shared+agent  | ref-library     |
      | agent-beta  | agent         | user-library    |
      | agent-gamma | all-agents    | conversational  |

  # --- CI integration -----------------------------------------------------

  Scenario: Gate-failure exit code surfaces actual versus expected values
    Given a benchmark suite where at least one declared gate fails
    When the operator runs the benchmark with gates enabled
    Then the process exits non-zero
    And the report identifies which gates failed
    And the report records the actual and expected values for each failed gate

  # --- Focus-area segmentation -------------------------------------------

  Scenario: Focus-area segmentation selects the requested subset
    Given a benchmark suite tagged with focus areas "release-gate" and "dogfood"
    When the operator runs the benchmark restricted to focus area "release-gate"
    Then only release-gate queries are scored
    And the report records which focus area was selected
