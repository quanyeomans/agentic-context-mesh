Feature: Curator health check
  As a kairix operator
  I want to verify the entity graph is healthy
  So that search results include accurate entity data

  Scenario: Healthy graph passes all checks
    Given Neo4j has 5 entities with vault_path and summary set
    When I run the curator health check
    Then missing_vault_path is empty
    And stale_entities is empty
    And total_entities equals 5
    And neo4j_available is true

  Scenario: Unavailable Neo4j returns graceful report
    Given Neo4j is unavailable
    When I run the curator health check
    Then neo4j_available is false
    And total_entities equals 0

  Scenario: Entity with missing vault_path is flagged
    Given Neo4j has an entity with no vault_path
    When I run the curator health check
    Then missing_vault_path is not empty
