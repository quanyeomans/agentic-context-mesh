Feature: kairix CLI routes through a warm MCP server when one is reachable
  As an operator running `kairix <subcommand>` against a deployment with
  a long-running MCP server, I want subcommands to reuse the server's
  warm pipeline + caches instead of paying cold-start every time
  So that interactive CLI calls return in seconds, not tens of seconds.

  Background:
    Given the CLI dispatcher is configured to inject a fake MCP client

  Scenario: Routes through MCP when the server is responsive
    Given the fake MCP server is responsive
    And the fake MCP envelope contains "result-from-warm-mcp"
    When the operator dispatches "search" with argv "agent-alpha needs --json"
    Then the dispatcher exits with code 0
    And the rendered output contains "result-from-warm-mcp"
    And the fake MCP server recorded a tool call to "search"

  Scenario: Falls back to in-process when the MCP server is not responsive
    Given the fake MCP server is not responsive
    When the operator dispatches "search" with argv "anything --json"
    Then the dispatcher returns no exit code
    And the fake MCP server recorded no tool call

  Scenario: Subcommands without an MCP equivalent stay in-process
    Given the fake MCP server is responsive
    When the operator dispatches "embed" with argv "--json"
    Then the dispatcher returns no exit code
    And the fake MCP server recorded no readiness probe
    And the fake MCP server recorded no tool call

  Scenario: Operator can disable routing globally
    Given the fake MCP server is responsive
    And CLI-to-MCP routing is disabled
    When the operator dispatches "search" with argv "topic --json"
    Then the dispatcher returns no exit code
    And the fake MCP server recorded no readiness probe
