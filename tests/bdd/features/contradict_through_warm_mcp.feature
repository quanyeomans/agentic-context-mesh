Feature: contradict through warm MCP renders correctly in text mode
  As an operator running `kairix contradict check <content>` against a warm MCP worker
  I want the text output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same contradiction report either way.

  Scenario: contradict envelope renders the same text as in-process contradict
    Given a contradict result with one hit at path "docs/sky.md"
    When the contradict result is converted to an MCP envelope and back via from_envelope
    Then the round-tripped text output is byte-identical to the original

  Scenario: kairix contradict --json emits the envelope dict to stdout
    Given a contradict use case that returns no hits for the input content
    When the operator runs the contradict CLI with json mode for content "agent-alpha claim"
    Then stdout is valid JSON containing keys content, contradictions, has_contradictions, and error
    And the contradict CLI exits with status 0
