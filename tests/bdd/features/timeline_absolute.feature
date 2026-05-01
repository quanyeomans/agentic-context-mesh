Feature: Agents can ask about specific months and dates
  As an AI agent helping a user review their work
  I need to ask about what happened in a specific month
  So that I can give them an accurate summary of that period

  Scenario: Agent asks about a specific month and gets the right date range
    When the agent calls tool_timeline with query "what happened in April 2026"
    Then the timeline response is_temporal is true
    And the timeline response time_window start contains "2026-04"
    And the timeline response error is empty

  Scenario: Agent asks about last week and the system understands
    When the agent calls tool_timeline with query "what changed last week in the project"
    Then the timeline response is_temporal is true
