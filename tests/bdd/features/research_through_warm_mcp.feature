Feature: research through warm MCP renders correctly in text mode
  As an operator running `kairix research <query>` against a warm MCP worker
  I want the text output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same synthesis either way.

  Scenario: research envelope renders the same text as in-process research
    Given a research result with synthesis "Plain answer" and confidence 0.6 for query "q"
    When the research result is converted to an MCP envelope and back via from_envelope
    Then the round-tripped research text output is byte-identical to the original

  Scenario: kairix research --json emits the envelope dict to stdout
    Given a research use case that returns synthesis "X" for query "qq"
    When the operator runs the research CLI with json mode
    Then research stdout is valid JSON containing keys query, synthesis, and error
    And the research CLI exits with status 0
