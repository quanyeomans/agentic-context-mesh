@connector @deadletter @wave-1-stub
Feature: Connector dead-letter handling
  As an operator monitoring an external knowledge source
  I want repeated failures on a single item to land in a dead-letter list
  So that I can review and replay poison items without stalling the rest of the batch

  Scenario: Dead-letter records failure with operator-reviewable error message
    Given a configured connector source named "alpha-source"
    And the source reports one pending change with identifier "broken-item"
    And fetching "broken-item" fails three times with the message "network reset"
    When the operator runs one pipeline batch for "alpha-source"
    Then a dead-letter row is recorded for "broken-item"
    And the dead-letter row carries the message "network reset"
    And the dead-letter row carries the source name "alpha-source"
    And the operator can list the dead-letter row from the command line

  @retry
  Scenario: Dead-letter retry counter increments on repeat failures
    Given a dead-letter row for "broken-item" with retry count 1
    And the source reports the same identifier "broken-item" in the next batch
    And fetching "broken-item" fails three times again
    When the operator runs one pipeline batch for "alpha-source"
    Then the dead-letter row for "broken-item" has retry count 2
    And the dead-letter row keeps the most recent failure message
