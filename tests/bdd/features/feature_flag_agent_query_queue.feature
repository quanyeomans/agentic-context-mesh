@feature_flag @agent_query_queue
Feature: Operator toggles the agent-query-queue feature flag
  As an operator running a kairix MCP server
  I want to choose whether tool_search routes through the new queue
  So that I can validate the carry-along delivery shape before cutting over

  ADR-029 G.1 — the flag gates the dispatch-or-queue path on tool_search.
  When OFF (default) tool_search runs synchronously as today and no
  pending_queries row is written. When ON the call routes through the
  background worker; fast calls still complete sync but the row is
  recorded as 'delivered' so carry-along stays sane.

  @happy_path @off
  Scenario: Flag OFF — tool_search runs synchronously and no row is written
    Given the operator has the agent-query-queue flag set to false
    When the agent calls tool_search via the queue-aware wrapper
    Then the response is the standard search envelope
    And no pending-queries row is written for the agent

  @happy_path @on
  Scenario: Flag ON — tool_search runs through the queue and records the row
    Given the operator has the agent-query-queue flag set to true
    When the agent calls tool_search via the queue-aware wrapper
    Then the response is the standard search envelope
    And exactly one delivered pending-queries row exists for the agent
