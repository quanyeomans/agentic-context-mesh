Feature: MCP agent temporal query handling
  As an AI agent calling tool_timeline
  I want date-relative queries correctly interpreted
  So that search results match the intended time window

  Scenario: "last week" is recognised as temporal
    When the agent calls tool_timeline with query "what happened last week"
    Then the timeline response is_temporal is true
    And the timeline response time_window has start and end dates
    And the timeline response error is empty

  Scenario: "yesterday" produces a single-day window
    When the agent calls tool_timeline with query "what changed yesterday" and anchor "2026-04-15"
    Then the timeline response is_temporal is true
    And the timeline response time_window start is "2026-04-14"
    And the timeline response time_window end is "2026-04-14"

  Scenario: Non-temporal query passes through unchanged
    When the agent calls tool_timeline with query "how does RRF fusion work"
    Then the timeline response is_temporal is false
    And the rewritten query equals the original query

  Scenario: Timeline tool never raises
    When the agent calls tool_timeline with query "!@#$%"
    Then no exception was raised
    And the timeline response is a valid dict
