@connector @pipeline @wave-1-stub
Feature: Connector pipeline orchestration
  As an operator with an external knowledge source
  I want connector batches to be atomic and recoverable
  So that partial failures do not corrupt my index or lose my cursor position

  Background:
    Given a configured connector source named "alpha-source"
    And an empty cursor for "alpha-source"

  Scenario: Connector pipeline runs one batch of changes end-to-end
    Given the source reports two pending changes since the cursor
    When the operator runs one pipeline batch for "alpha-source"
    Then both changes appear in the bronze store in fetch order
    And both changes appear as chunks in the silver index
    And the cursor for "alpha-source" advances past both items
    And the batch commit is recorded as a single atomic transaction

  @recovery
  Scenario: Connector pipeline rolls back transaction on silver failure
    Given the source reports one pending change since the cursor
    And the silver processor will fail on the next document
    When the operator runs one pipeline batch for "alpha-source"
    Then the bronze store contains no new records for "alpha-source"
    And the silver index contains no new chunks for "alpha-source"
    And the cursor for "alpha-source" is unchanged

  @recovery
  Scenario: Connector pipeline records dead-letter row on third fetch failure
    Given the source reports one pending change with identifier "poison-item"
    And fetching "poison-item" fails three times in a row
    When the operator runs one pipeline batch for "alpha-source"
    Then a dead-letter row is recorded for "poison-item" with an operator-reviewable message
    And the cursor for "alpha-source" advances past "poison-item"
    And the operator can list the dead-letter row from the command line
