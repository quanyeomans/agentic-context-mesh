Feature: MCP agent prep tool
  As an AI agent calling tool_prep
  I want a quick grounded summary before a full search
  So that I can decide whether to invest in deeper research

  Scenario: L0 prep returns a short summary with sources
    Given the search returns documents about "architecture"
    And the LLM returns a summary
    When the agent calls tool_prep with query "architecture decision record" at tier "l0"
    Then the prep response has a non-empty summary
    And the prep response tier is "l0"
    And the prep response error is empty

  Scenario: Prep with no matching documents returns informative message
    Given the search returns no documents
    When the agent calls tool_prep with query "quantum marine biology" at tier "l0"
    Then the prep response summary indicates no relevant documents
    And the prep response error is empty

  Scenario: Prep tool never raises to the caller
    Given the search raises an error
    When the agent calls tool_prep with query "anything" at tier "l0"
    Then no prep exception was raised
    And the prep response is a valid dict
