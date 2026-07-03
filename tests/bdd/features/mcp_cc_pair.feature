@mcp @cc_pair @topology
Feature: tool_cc_pair MCP tool — escalation envelope for Wave D cc_pair lifecycle
  As an agent connected over MCP
  I want a tool_cc_pair call to return the OperatorOnlyCapability envelope
  with the exact `kairix cc-pair` command for my operator to run
  So that lifecycle mutations stay audited at the operator surface
  while I get the routing affordance, not silent failure.

  @happy_path
  Scenario: tool_cc_pair list returns the operator-only envelope with the friendly command
    Given the MCP server is constructed
    When the agent calls tool_cc_pair with verb "list"
    Then the MCP cc_pair envelope contains capability "cc-pair"
    And the MCP cc_pair envelope contains operator_command "kairix cc-pair list"
    And the MCP cc_pair envelope contains reason "topology"

  Scenario: tool_cc_pair pause returns the friendly pause command in the envelope
    Given the MCP server is constructed
    When the agent calls tool_cc_pair with verb "pause"
    Then the MCP cc_pair envelope contains operator_command "kairix cc-pair pause"
