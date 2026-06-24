Feature: kairix dead-letter drain — clear the orphaned-source backlog
  As a kairix operator who removed a connector but still sees its poisoned
  dead-letters piling up in connector_deadletter
  I want a single command that drains the permanently-unprocessable backlog
  So that an orphaned source's noise stops inflating the failed counter
  even though no active connector will ever drain it

  Scenario: Drain one orphaned source — its permanently-unprocessable row clears
    Given a kairix database with a drainable dead-letter for an orphaned source
    When the operator runs the kairix dead-letter drain command for that source
    Then the drain stdout reports one row drained for that source
    And the orphaned source has no remaining dead-letter rows
    And the drain command exits with code 0

  Scenario: Drain every source — all distinct sources are swept
    Given a kairix database with drainable dead-letters for two orphaned sources
    When the operator runs the kairix dead-letter drain command for all sources
    Then the drain stdout reports a total across two sources
    And both orphaned sources have no remaining dead-letter rows
    And the drain command exits with code 0

  Scenario: Dry-run reports what would drain without mutating
    Given a kairix database with a drainable dead-letter for an orphaned source
    When the operator runs the kairix dead-letter drain command in dry-run mode
    Then the drain stdout reports what would drain in dry-run mode
    And the orphaned source still has its dead-letter row
    And the drain command exits with code 0
