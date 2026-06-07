Feature: prep through warm MCP renders correctly in text mode
  As an operator running `kairix prep <query>` against a warm MCP worker
  I want the text output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same summary either way.

  Scenario: prep envelope renders the same text as in-process prep
    Given a prep result with summary "Alpha overview" and sources "doc-alpha,doc-beta"
    When the prep result is converted to an MCP envelope and back via from_envelope
    Then the round-tripped prep text output is byte-identical to the original

  Scenario: kairix prep --json emits the envelope dict to stdout
    Given a prep use case that returns summary "X" for query "topic-q"
    When the operator runs the prep CLI with json mode
    Then prep stdout is valid JSON containing keys query, tier, summary, and error
    And the prep CLI exits with status 0
