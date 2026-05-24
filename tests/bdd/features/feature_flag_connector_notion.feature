@feature_flag @connector_notion
Feature: Operator toggles the Notion connector feature flag
  As an operator running a kairix engagement container
  I want to choose whether the Notion connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector at the dispatch boundary. When OFF the
  connector slot is a no-op (zero counters); when ON the standard
  connector pipeline resolves the notion plugin via its
  entry-point and runs one sync tick. See
  docs/architecture/feature-flag-architecture.md §7.

  @happy_path @off
  Scenario: Flag OFF — the Notion connector slot is a no-op
    Given the operator has the notion connector flag set to false
    When the worker notion connector sync tick runs
    Then the notion connector OFF branch log appears
    And the notion connector ON branch does not run

  @happy_path @on
  Scenario: Flag ON — the Notion connector pipeline runs
    Given the operator has the notion connector flag set to true
    When the worker notion connector sync tick runs
    Then the notion connector ON branch log appears
    And the notion connector OFF branch does not run
