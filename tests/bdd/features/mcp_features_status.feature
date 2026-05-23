Feature: tool_features_status — MCP feature-flag introspection
  As an AI agent connected to the kairix MCP server
  I want a tool that returns the registered feature flags + their effective values
  So that I can self-introspect what behaviour is enabled before acting

  Scenario: Live registry returns an envelope with the flags list populated
    Given the kairix features registry has entries declared
    When the agent calls the tool_features_status MCP tool
    Then the tool_features_status envelope carries a non-empty flags list
    And the tool_features_status envelope has an empty error string

  Scenario: Envelope shape mirrors the CLI --json output
    Given the kairix features registry has entries declared
    When the agent calls the tool_features_status MCP tool
    Then the tool_features_status envelope has a flags key
    And the tool_features_status envelope has an error key
