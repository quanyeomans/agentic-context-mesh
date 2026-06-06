Feature: timeline through warm MCP renders correctly in text mode
  As an operator running `kairix timeline <query>` against a warm MCP worker
  I want the text output to match the in-process path byte-for-byte
  So that warm-MCP routing is invisible to me and I read the same timeline either way.

  Scenario: empty timeline envelope renders the same text as in-process timeline
    Given a timeline result with no hits for query "what happened nowhere"
    When the timeline is converted to an MCP envelope and back via from_envelope
    Then the round-tripped timeline text output is byte-identical to the original

  Scenario: populated timeline envelope across multiple agents round-trips byte-for-byte
    Given a timeline result with hits across agents alpha, beta, and gamma in April 2026
    When the timeline is converted to an MCP envelope and back via from_envelope
    Then the round-tripped timeline text output is byte-identical to the original
    And the rebuilt timeline names every agent path

  Scenario: date-filtered timeline envelope preserves the window header
    Given a timeline result with window 2026-05-30 to 2026-06-06 and one hit
    When the timeline is converted to an MCP envelope and back via from_envelope
    Then the round-tripped timeline header carries both window dates

  Scenario: kairix timeline --json emits the envelope dict to stdout
    Given a timeline use case returning a fixed two-hit result for query "topic"
    When the operator runs the timeline CLI with json mode for query "topic"
    Then timeline stdout is valid JSON containing keys original_query, results, time_window, and error
    And the timeline CLI exits with status 0
