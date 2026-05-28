@curator @drain @gh334
Feature: GH #334 — drain entity_signals into Neo4j
  As an operator running the kairix worker
  I want the staged entity_signals rows to be pushed into Neo4j on a bounded cadence
  So that entity-aware retrieval operates against a populated graph
  (Background: production had 2,278,272 un-pushed rows over years; no code flipped the flag.)

  @happy_path
  Scenario: One drain tick pushes every staged person signal
    Given the operator has staged three person signals into entity_signals
    And the graph backend is reachable
    When the operator runs one drain tick with batch_size 500
    Then the result reports pushed equal to 3
    And every staged signal has pushed_to_neo4j flipped to 1
    And the graph backend received three MERGE Person calls

  Scenario: Neo4j unreachable — drain tick is a no-op
    Given the operator has staged three person signals into entity_signals
    And the graph backend is unreachable
    When the operator runs one drain tick with batch_size 500
    Then the result reports neo4j_available is false
    And the result reports pushed equal to 0
    And no staged signal has pushed_to_neo4j flipped

  Scenario: Partial per-row failure — first commits, second marked failed, third commits
    Given the operator has staged three person signals named alpha, bravo, charlie
    And the graph backend raises on the value bravo
    When the operator runs one drain tick with batch_size 500
    Then the signal named alpha has pushed_to_neo4j equal to 1
    And the signal named bravo has pushed_to_neo4j equal to -1
    And the signal named bravo carries a non-empty last_push_error
    And the signal named charlie has pushed_to_neo4j equal to 1

  Scenario: Retry backoff — signal at attempt-count 3 is skipped on the next tick
    Given the operator has staged one person signal with push_attempt_count already 3
    And the graph backend is reachable
    When the operator runs one drain tick with batch_size 500
    Then the result reports pushed equal to 0
    And the stalled signal still has pushed_to_neo4j equal to -1

  Scenario: Age priority — oldest signals drain first when batch_size is smaller than backlog
    Given the operator has staged five person signals with mixed modified_at timestamps
    And the graph backend is reachable
    When the operator runs one drain tick with batch_size 2
    Then the result reports pushed equal to 2
    And the two oldest staged signals have pushed_to_neo4j equal to 1
    And the three newest staged signals still have pushed_to_neo4j equal to 0
