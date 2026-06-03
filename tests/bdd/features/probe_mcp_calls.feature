Feature: Probe mcp-calls — operator inspection of mcp_call_log
  As a kairix operator
  I want to inspect the mcp_call_log per-tool observability table
  So that I can investigate brief failures, latency tails, and which tools are slow

  Scenario: probe mcp-calls renders a table when calls have landed
    Given the mcp_call_log table has rows for two tools
    When the operator runs probe mcp-calls
    Then the report lists both tool names
    And each tool row shows count, p50, p95, p99, success rate, and top errors

  Scenario: probe mcp-calls reports an empty window when no rows match
    Given the mcp_call_log table is empty
    When the operator runs probe mcp-calls
    Then the report says no calls recorded

  Scenario: probe mcp-calls emits a JSON envelope under --json
    Given the mcp_call_log table has rows for one tool
    When the operator runs probe mcp-calls with the json flag
    Then stdout is a valid JSON object with a tools array

  Scenario: probe mcp-calls rejects a malformed --since duration
    Given the mcp_call_log table is empty
    When the operator runs probe mcp-calls with --since potato
    Then the CLI exits with code 2
    And stderr names the accepted shape
