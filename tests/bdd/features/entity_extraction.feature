Feature: Entity extraction from reference library
  As a kairix maintainer
  I want to extract entities from curated documents
  So that the knowledge graph is populated on install

  Scenario: Known entities are extracted
    Given a document titled "Meditations" by Marcus Aurelius
    When I run entity extraction
    Then an entity named "Marcus Aurelius" is found
    And an entity of type "Publication" named "Meditations" is found

  Scenario: Entity relationships are created
    Given extracted entities include a person and their work
    When I resolve relationships
    Then an AUTHORED_BY edge exists between the work and person
