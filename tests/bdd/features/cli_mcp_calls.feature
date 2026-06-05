Feature: kairix mcp-calls — operator inspection of mcp_call_log
  As a kairix operator
  I want to inspect the mcp_call_log per-tool observability table
  So that I can investigate brief failures, latency tails, and which tools are slow

  Scenario: the mcp-calls command renders a table when calls have landed
    Given the mcp_call_log table has rows for two tools
    When the operator runs the mcp-calls command
    Then the report lists both tool names
    And each tool row shows count, p50, p95, p99, success rate, and top errors

  Scenario: the mcp-calls command reports an empty window when no rows match
    Given the mcp_call_log table is empty
    When the operator runs the mcp-calls command
    Then the report says no calls recorded

  Scenario: the mcp-calls command emits a JSON envelope under --json
    Given the mcp_call_log table has rows for one tool
    When the operator runs the mcp-calls command with the json flag
    Then stdout is a valid JSON object with a tools array

  Scenario: the mcp-calls command rejects a malformed --since duration
    Given the mcp_call_log table is empty
    When the operator runs the mcp-calls command with --since potato
    Then the CLI exits with code 2
    And stderr names the accepted shape
