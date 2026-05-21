Feature: MCP agent ingests a chat transcript
  As an AI agent driving the conversation paradigm via kairix
  I want to push a JSONL chat transcript into the knowledge store
  So that later search and recall can see what the agent just did

  Scenario: Happy path — agent ingests its conversation into the assigned namespace
    Given the agent is scoped to namespace "engagement-alpha"
    And the agent has a transcript with 5 turns about "Alice"
    When the agent calls ingest-chat with namespace "engagement-alpha"
    Then the ingest response reports 5 turns ingested
    And the ingest response namespace is "engagement-alpha"
    And the ingest response error is empty

  Scenario: Cross-engagement namespace is rejected
    Given the agent is scoped to namespace "engagement-alpha"
    And the agent has a transcript with 5 turns about "Alice"
    When the agent calls ingest-chat with namespace "engagement-beta"
    Then the ingest response error contains "CrossEngagementNamespace"
    And the ingest response reports 0 turns ingested
