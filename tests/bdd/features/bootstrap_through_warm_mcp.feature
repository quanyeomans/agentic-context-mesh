Feature: bootstrap through warm MCP renders correctly in text mode
  As an operator running `kairix bootstrap <agent>` against a warm MCP worker
  I want the markdown output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same envelope either way.

  Scenario: bootstrap envelope renders the same markdown as the in-process result
    Given a bootstrap result with role "Builder" board "priorities: ship" and one memory entry
    When the bootstrap result is converted to an MCP envelope and back via from_envelope
    Then the round-tripped markdown is byte-identical to the original

  Scenario: kairix bootstrap --json emits the envelope dict to stdout
    Given a bootstrap use case that returns role "Shape" for "agent-alpha"
    When the operator runs the bootstrap CLI with json mode for the agent
    Then bootstrap stdout is valid JSON containing keys agent, role, and health
    And the bootstrap CLI exits with status 0
