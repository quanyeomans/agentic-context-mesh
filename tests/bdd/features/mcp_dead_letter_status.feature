Feature: tool_dead_letter_status MCP tool — agent-facing dead-letter triage
  As an agent investigating why a connector is stuck
  I want to call tool_dead_letter_status and receive the per-source breakdown
  So that I can advise the operator on retry vs upstream fix vs escalate

  Scenario: Empty dead-letter table — agent sees a zero-row envelope
    Given an MCP-bound kairix database with no dead-letter rows
    When the agent calls the tool_dead_letter_status MCP tool
    Then the MCP envelope has total zero and an empty per_source list

  Scenario: Populated dead-letter table — agent sees the structured envelope
    Given an MCP-bound kairix database seeded with dead-letter rows for one connector
    When the agent calls the tool_dead_letter_status MCP tool
    Then the MCP envelope has total greater than zero and a non-empty per_source list
    And the per_source entry exposes failure_count, failure_class, and mime buckets
