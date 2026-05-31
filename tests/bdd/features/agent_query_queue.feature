@agent_query_queue
Feature: Agent-facing query queue surfaces slow tool calls without breaking the agent
  As an agent calling kairix tool_search
  I want slow calls to return a plain-text "Processing..." reply (never an error envelope)
  So that my own fault-tolerance heuristic doesn't fire and I keep talking to kairix

  ADR-029 G.1 spike — when the agent_query_queue flag is on, tool_search
  calls that exceed the synchronous budget (1.5 s default) are queued on
  a background worker thread and the response is a plain text message
  containing the new query id. The next call from the same agent_id
  receives the prior result as a "carry-along" prefix on its own
  envelope. See docs/architecture/ADR-029-agent-query-queue-and-carry-along-delivery.md.

  @happy_path
  Scenario: Slow tool_search returns plain text, then carries result on next call
    Given the agent_query_queue flag is on
    And the search handler takes longer than the synchronous budget
    When the agent makes a slow tool_search call
    Then the response is the plain text "Processing your request" message
    And no error envelope is returned
    When the agent makes a second tool_search call from the same agent_id
    Then the second response carries the prior result as a prefix
