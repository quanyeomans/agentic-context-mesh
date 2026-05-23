@cli @cc_pair @topology_v2
Feature: kairix cc-pair — operator surface over the topology v2 cc_pair lifecycle
  As a kairix operator promoting the topology v2 connector / collection / scope
  topology migration (per docs/architecture/connector-scope-topology/ADR.md)
  I want a CLI to list, create, pause, resume, and delete cc_pairs
  So that I can operate the lifecycle from the same surface as `kairix features status`
  without writing SQL.

  The verbs route through the Wave C lifecycle service
  (kairix/core/connectors/cc_pair.py) — illegal transitions surface as
  operator-friendly error messages, not Python tracebacks.

  Background:
    Given a fresh kairix sqlite database with the topology v2 schema applied

  @happy_path
  Scenario: cc-pair list reports the friendly empty-state line when nothing is declared
    When the operator runs the kairix cc-pair list command
    Then the cc-pair output contains "No cc_pairs declared"
    And the cc-pair command exits with code 0

  @happy_path
  Scenario: cc-pair create inserts a fresh row at status SCHEDULED
    Given the operator has registered the connector "obsidian-personal-conn"
    When the operator runs the kairix cc-pair create command with name "obsidian-personal"
    Then the cc-pair output contains "status=SCHEDULED"
    And the cc-pair command exits with code 0

  Scenario: cc-pair pause rejects an illegal transition with an operator-friendly message
    Given the operator has registered the connector "obsidian-personal-conn"
    And the operator created a cc_pair "obsidian-personal" at status SCHEDULED
    When the operator runs the kairix cc-pair pause command for that cc_pair
    Then the cc-pair stderr contains "illegal transition"
    And the cc-pair command exits with a non-zero code

  Scenario: cc-pair resume from PAUSED transitions back to ACTIVE
    Given the operator has registered the connector "obsidian-personal-conn"
    And the operator created a cc_pair "obsidian-personal" at status SCHEDULED
    And the operator advanced that cc_pair through INITIAL_INDEXING and ACTIVE and PAUSED
    When the operator runs the kairix cc-pair resume command for that cc_pair
    Then the cc-pair output contains "ACTIVE"
    And the cc-pair command exits with code 0

  Scenario: cc-pair delete transitions to DELETING
    Given the operator has registered the connector "obsidian-personal-conn"
    And the operator created a cc_pair "obsidian-personal" at status SCHEDULED
    When the operator runs the kairix cc-pair delete command for that cc_pair
    Then the cc-pair output contains "DELETING"
    And the cc-pair command exits with code 0
