@connector @cursor @wave-1-stub
Feature: Connector cursor store
  As an operator running incremental syncs from external sources
  I want each source's cursor position to advance only when the batch commits
  So that a crashed or rolled-back batch leaves the cursor at a replayable point

  Scenario: Cursor advance is atomic with batch commit
    Given a configured connector source named "alpha-source"
    And the cursor for "alpha-source" is at position "cursor-1"
    And the source reports one pending change since "cursor-1"
    When the operator runs one pipeline batch for "alpha-source"
    Then the cursor for "alpha-source" advances to a position after "cursor-1"
    And the cursor advance and the batch commit land in the same transaction

  @lookup
  Scenario: Cursor read returns nothing for an unknown source
    Given no cursor has ever been recorded for the source "ghost-source"
    When the operator reads the cursor for "ghost-source"
    Then the cursor read reports that no cursor is recorded for "ghost-source"
