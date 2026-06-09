@entity_summary @capability_457
Feature: Operator searches find content via entity descriptions
  As an operator running kairix with enriched entities
  I want search to surface entities by their Wikidata descriptions
  So that "AI ethics organisations" finds entities tagged with that role

  ADR-036 (#457) — when ``entity_summary_indexing_enabled`` is ON, the
  worker tick projects Neo4j ``n.summary`` content into the synthetic
  ``entity-summaries`` collection so descriptions reach BM25 + vector
  retrieval. Default OFF preserves pre-#457 behaviour.

  @happy_path @on
  Scenario: Description-keyword query surfaces an enriched entity
    Given an entity 'Ada Lovelace Institute' enriched with description 'AI policy research institute'
    And the entity-summary-indexing flag is true
    And the worker has run a projector tick
    When the operator searches for 'AI policy research institute'
    Then the results include a chunk with source uri prefix 'entity://'

  @off
  Scenario: Description-keyword query returns no entity row when the flag is off
    Given an entity 'Ada Lovelace Institute' enriched with description 'AI policy research institute'
    And the entity-summary-indexing flag is false
    And the worker has run a projector tick
    When the operator searches for 'AI policy research institute'
    Then no result has a source uri prefix 'entity://'
