Feature: kairix dead-letter status — operator triage view
  As a kairix operator triaging a stuck connector during an incident
  I want a single command that summarises the connector_deadletter table
  So that I can decide whether to re-extract, fix upstream code, or escalate

  Scenario: Empty dead-letter table — operator sees a friendly empty-state line
    Given a fresh kairix database with no dead-letter rows
    When the operator runs the kairix dead-letter status command
    Then the dead-letter stdout reports an empty triage summary
    And the dead-letter command exits with code 0

  Scenario: Populated dead-letter table — operator sees per-source breakdown
    Given a kairix database seeded with dead-letter rows for one connector
    When the operator runs the kairix dead-letter status command
    Then the dead-letter stdout reports the per-source breakdown
    And the dead-letter command exits with code 0

  Scenario: JSON output emits the canonical envelope shape
    Given a kairix database seeded with dead-letter rows for one connector
    When the operator runs the kairix dead-letter status command with the --json flag
    Then the dead-letter stdout parses as JSON with a total key and a per_source list
    And the dead-letter command exits with code 0
