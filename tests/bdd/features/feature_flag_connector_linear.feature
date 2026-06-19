@feature_flag @connector_linear
Feature: Operator toggles the Linear connector feature flag
  As an operator running a kairix deployment
  I want to choose whether the Linear connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector at the dispatch boundary. When OFF the
  connector slot is a no-op (zero counters); when ON the standard
  connector pipeline resolves the linear plugin via its entry-point and
  runs one sync tick. See docs/architecture/feature-flag-architecture.md §7.

  @happy_path @off
  Scenario: Flag OFF — the Linear connector slot is a no-op
    Given the operator has the linear connector flag set to false
    When the worker linear connector sync tick runs
    Then the linear connector OFF branch log appears
    And the linear connector ON branch does not run

  @happy_path @on
  Scenario: Flag ON — the Linear connector pipeline runs
    Given the operator has the linear connector flag set to true
    When the worker linear connector sync tick runs
    Then the linear connector ON branch log appears
    And the linear connector OFF branch does not run
