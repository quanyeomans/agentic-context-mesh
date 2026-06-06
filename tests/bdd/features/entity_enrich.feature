Feature: kairix entity enrich
  As an operator who has resolved Wikidata QIDs onto entities
  I want `kairix entity enrich` to fetch the canonical description and write it
  to n.summary in Neo4j
  So that the entity audit + store health checks see populated summaries
  without me hand-curating each entity.

  Scenario: enrich without a target flag fails with argparse usage error
    When the operator runs the entity CLI with `enrich`
    Then the entity CLI exits with status 2

  Scenario: enrich --name degrades gracefully without Neo4j
    When the operator runs the entity CLI with `enrich --name "Acme Corp"`
    Then the entity CLI exits with status 0
    And stdout shows the entity enrich skip notice

  Scenario: enrich --all-missing degrades gracefully without Neo4j
    When the operator runs the entity CLI with `enrich --all-missing --limit 1`
    Then the entity CLI exits with status 0
    And stdout shows the batch enrichment summary
