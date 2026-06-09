@feature_flag @entity_summary_indexing_enabled
Feature: Operator toggles the entity-summary-indexing feature flag
  As an operator running kairix with enriched entities
  I want to choose whether the worker projects Neo4j n.summary into the chunk store
  So that I can validate the cutover against my eval suite before flipping production

  ADR-036 — the flag gates the EntitySummaryProjectorStage on every
  worker tick. When OFF (the default) the stage skips entirely; no
  Neo4j queries, no chunk-writer calls. When ON the stage projects up
  to per_tick_max_items entities per tick into the synthetic
  ``entity-summaries`` collection so Wikidata descriptions participate
  in BM25 + vector retrieval.

  @happy_path @off
  Scenario: Flag OFF — the projector never ticks
    Given the operator has the entity-summary-indexing flag set to false
    When the worker tick stage evaluates whether to run the projector
    Then the projector is not ticked

  @happy_path @on
  Scenario: Flag ON — the projector ticks once per worker tick
    Given the operator has the entity-summary-indexing flag set to true
    When the worker tick stage evaluates whether to run the projector
    Then the projector is ticked once
