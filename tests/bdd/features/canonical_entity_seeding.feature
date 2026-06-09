@canonical_entities @issue_431
Feature: Operator-declared canonical entities propagate to Neo4j
  As an operator with platform-canon entities (agents, components, organisations)
  I want them seeded into Neo4j as canonical nodes
  So that retrieval and entity-suggest don't flag them as "new" or "junk"

  Issue #431 — the ``CanonicalEntity`` parser reads operator
  declarations from ``kairix.config.yaml``; the seeder upserts each
  into Neo4j with ``kairix_canonical: True`` so downstream code can
  distinguish canon from discovered entities.

  @happy_path @on
  Scenario: A declared canonical entity reaches Neo4j with kairix_canonical=true
    Given the operator has declared entity 'Shape' of type 'agent' with summary 'Strategic agent'
    When the worker startup seeds canonical entities into Neo4j
    Then Neo4j receives an upsert for 'Shape' under the 'agent' label
    And the upsert carries kairix_canonical=true

  @happy_path
  Scenario: Aliases declared by the operator land on the Neo4j node
    Given the operator has declared entity 'Acme Corp' of type 'organisation' with aliases 'Acme,Acme Inc.'
    When the worker startup seeds canonical entities into Neo4j
    Then the upsert props include the aliases list

  Scenario: A degraded Neo4j leaves zero seeded and the operator can re-run
    Given Neo4j is unavailable for canonical seeding
    And the operator has declared entity 'Shape' of type 'agent' with summary 'Strategic agent'
    When the worker startup seeds canonical entities into Neo4j
    Then zero entities are seeded
    And no upserts land
