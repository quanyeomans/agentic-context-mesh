Feature: MCP agent asks what kairix knows about an entity
  As an AI agent introspecting kairix's current knowledge
  I want to ask for the facts about a named entity
  So that I can ground my answer in the team's current ground truth

  Scenario: Happy path — known entity returns the current facts
    Given the fact store has a fact about "Alice" with attribute "role" and value "founder"
    When the agent calls facts-about with entity "Alice"
    Then the facts response lists 1 hit
    And the facts response hit value is "founder"
    And the facts response error is empty

  Scenario: Unknown entity returns an empty list, not an error
    Given the fact store has no facts about "Nonexistent"
    When the agent calls facts-about with entity "Nonexistent"
    Then the facts response lists 0 hits
    And the facts response error is empty
