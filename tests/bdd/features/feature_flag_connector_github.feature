@feature_flag @connector_github
Feature: Operator toggles the GitHub connector feature flag
  As an operator running a kairix engagement container
  I want to choose whether the GitHub connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector at the dispatch boundary. When OFF the
  connector slot is a no-op (zero counters); when ON the standard
  connector pipeline resolves the github plugin via its entry-point
  and runs one sync tick. See
  docs/architecture/feature-flag-architecture.md section 7.

  @happy_path @off
  Scenario: Flag OFF the github connector slot is a no-op
    Given the operator has the github connector flag set to false
    When the worker github connector sync tick runs
    Then the github connector OFF branch log appears
    And the github connector ON branch does not run

  @happy_path @on
  Scenario: Flag ON the github connector pipeline runs
    Given the operator has the github connector flag set to true
    When the worker github connector sync tick runs
    Then the github connector ON branch log appears
    And the github connector OFF branch does not run
