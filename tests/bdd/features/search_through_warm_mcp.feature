Feature: search through warm MCP renders correctly in text mode
  As an operator running `kairix search "query"` against a warm MCP worker
  I want the text output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same search results either way.

  Scenario: search envelope renders the same text as in-process search
    Given a SearchOutput with 3 hits
    When the search output is converted to an MCP envelope and back via from_envelope
    Then format_text on the round-tripped result is byte-identical to format_text on the original

  Scenario: kairix search --json emits the envelope dict to stdout
    Given a search use case that returns 2 hits for query "agent-alpha sync"
    When the operator runs the search CLI with json mode
    Then stdout is valid JSON containing keys query, intent, and results
    And the search CLI exits with status 0
