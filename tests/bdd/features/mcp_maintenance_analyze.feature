Feature: tool_maintenance_analyze MCP tool
  As an agent connected to the kairix MCP server
  I want a `tool_maintenance_analyze` MCP tool
  So that I can refresh SQLite query-planner statistics without escalating
  to an operator

  Scenario: Agent invokes tool_maintenance_analyze on a seeded index
    Given a kairix process configured with FakePaths and an MCP-callable index
    When the agent calls tool_maintenance_analyze with a tmp db_path
    Then the envelope reports analyze_ran true with a non-empty reason
    And the envelope carries rows_analyzed elapsed_ms and plan samples

  Scenario: Agent invokes tool_maintenance_analyze on an unreachable path
    Given a kairix index path that cannot be opened
    When the agent calls tool_maintenance_analyze on the unreachable path
    Then the envelope reports an error and analyze_ran false
