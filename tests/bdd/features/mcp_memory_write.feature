Feature: MCP agent writes a memory to the knowledge store
  As an AI agent connected to kairix over MCP
  I want a tool that saves what I just learned
  So that the team's knowledge store remembers it and search can find it

  Scenario: Happy path — a configured agent saves a note and it is on disk
    Given agent-alpha is registered in the team's agent configuration
    When the agent writes the memory "rule: always check the board first" for agent-alpha
    Then the memory-write response reports no error
    And the memory-write response names a saved file under agent-alpha's memory area
    And the memory-write response says the memory is searchable now

  Scenario: An unregistered agent is rejected with configuration guidance
    Given agent-alpha is registered in the team's agent configuration
    When the agent writes the memory "a stray thought" for agent-omega
    Then the memory-write response is an error naming agent-omega
    And the memory-write error tells the operator to add the agent to the configuration
    And no memory file was written

  Scenario: A memory is saved even while kairix is still warming up
    Given agent-alpha is registered in the team's agent configuration
    And kairix has not finished warming up so search indexing is not ready yet
    When the agent writes the memory "decision: adopt the new ranking" for agent-alpha
    Then the memory-write response reports no error
    And the memory-write response names a saved file under agent-alpha's memory area
    And the memory-write response says the memory is saved and queued for indexing
