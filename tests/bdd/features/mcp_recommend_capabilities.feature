Feature: An agent asks kairix which capability fits a task over MCP
  As an AI agent connected to kairix over MCP
  I want to call one tool with a task description
  So that I get back the ranked capability to reach for next, ready to call

  The recommend_capabilities MCP tool is the agent-facing surface for the
  recommender. It is read-only and gated by the recommender feature flag.

  Scenario: The tool returns a ranked capability with its invocation
    Given the recommender is turned on for the MCP surface
    And the MCP toolset includes a way to check content for conflicts
    When the agent asks the recommend tool which capability fits "check this against what we know"
    Then the tool returns the conflict-checking capability first
    And the tool response reports no error

  Scenario: The tool reports it is disabled when the flag is off
    Given the recommender is turned off for the MCP surface
    When the agent asks the recommend tool which capability fits "anything at all"
    Then the tool response says the recommender is disabled
