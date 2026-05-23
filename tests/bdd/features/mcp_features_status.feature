Feature: tool_features_status — MCP feature-flag introspection
  As an AI agent connected to the kairix MCP server
  I want a tool that returns the registered feature flags + their effective values
  So that I can self-introspect what behaviour is enabled before acting

  Scenario: Empty registry returns an envelope with an empty flags list
    Given the kairix features registry has no entries declared
    When the agent calls the tool_features_status MCP tool
    Then the tool_features_status envelope carries an empty flags list
    And the tool_features_status envelope has an empty error string

  Scenario: Envelope shape mirrors the CLI --json output
    Given the kairix features registry has no entries declared
    When the agent calls the tool_features_status MCP tool
    Then the tool_features_status envelope has a flags key
    And the tool_features_status envelope has an error key
