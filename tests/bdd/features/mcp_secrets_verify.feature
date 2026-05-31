Feature: tool_secrets_verify — agent-callable credential preflight
  As an agent operating against a deployed kairix
  I want a read-only tool that reports which kairix-bound secrets resolve
  And which canonical KV names are missing
  So that I can answer "is auth healthy?" without docker exec access
  And so that the operator-facing alias map never leaks to agents

  Scenario: Verify envelope shape includes every required field
    Given the agent calls tool_secrets_verify with no arguments
    When the MCP tool returns its envelope
    Then the envelope carries the secrets list field
    And the envelope carries the missing_count field
    And the envelope carries the legacy_alias_count field
    And the envelope carries the error field

  Scenario: Verify redacts legacy_used so the alias map cannot leak
    Given the agent calls tool_secrets_verify with no arguments
    When the MCP tool returns its envelope
    Then no row in the secrets list carries the legacy_used field
    And every row carries the canonical_kv field
    And every row carries the status field
