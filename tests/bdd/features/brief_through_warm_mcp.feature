Feature: brief through warm MCP renders correctly in text mode
  As an operator running `kairix brief <agent>` against a warm MCP worker
  I want the text output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same briefing either way.

  Scenario: brief envelope renders the same text as in-process brief
    Given a brief result with content "Briefing for agent-alpha" written to "/tmp/brief.md"
    When the brief is converted to an MCP envelope and back via from_envelope
    Then the round-tripped text output is byte-identical to the original

  Scenario: kairix brief --json emits the envelope dict to stdout
    Given a brief use case that returns content "X" at path "/p/m.md"
    When the operator runs the brief CLI with json mode for agent-alpha
    Then stdout is valid JSON containing keys content, path, and error
    And the brief CLI exits with status 0

  Scenario: brief emits structured source citations the agent can re-open
    Given a brief use case that retrieves three sources for agent-alpha
    When the configured agent is briefed through the use case
    Then the brief envelope carries the three source breadcrumbs
    And each breadcrumb exposes a resolvable source_uri
    And the brief content ends with a Sources footer listing the citations
