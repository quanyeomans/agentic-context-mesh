Feature: Operator asks what kairix knows about an entity from the shell
  As an operator (or an agent shelling out) introspecting kairix's knowledge
  I want a facts-about command that reports what is known about a named entity
  So that the CLI mirrors the facts_about MCP tool (CLI/MCP parity)

  Scenario: Happy path — the command reports the entity's known facts
    Given the knowledge store holds a fact that "Acme Corp" has industry "manufacturing"
    When the operator runs facts-about for "Acme Corp"
    Then the facts-about command succeeds
    And the facts-about output reports "manufacturing"

  Scenario: An entity with an indexed summary but no facts still gets an answer
    Given the knowledge store holds an entity summary for "Globex" that reads "Globex makes gadgets"
    When the operator runs facts-about for "Globex"
    Then the facts-about command succeeds
    And the facts-about output reports "gadgets"

  Scenario: An unknown entity reports no facts without failing
    Given the knowledge store holds a fact that "Acme Corp" has industry "manufacturing"
    When the operator runs facts-about for "Nonexistent Co"
    Then the facts-about command succeeds
    And the facts-about output reports no facts
