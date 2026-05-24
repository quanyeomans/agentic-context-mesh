@feature_flag @connector_sharepoint
Feature: Operator toggles the SharePoint connector feature flag
  As an operator running a kairix engagement container
  I want to choose whether the SharePoint connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector at the dispatch boundary. When OFF the
  connector slot is a no-op (zero counters); when ON the standard
  connector pipeline resolves the sharepoint plugin via its
  entry-point and runs one sync tick. See
  docs/architecture/feature-flag-architecture.md §7.

  @happy_path @off
  Scenario: Flag OFF — the SharePoint connector slot is a no-op
    Given the operator has the sharepoint connector flag set to false
    When the worker sharepoint connector sync tick runs
    Then the sharepoint connector OFF branch log appears
    And the sharepoint connector ON branch does not run

  @happy_path @on
  Scenario: Flag ON — the SharePoint connector pipeline runs
    Given the operator has the sharepoint connector flag set to true
    When the worker sharepoint connector sync tick runs
    Then the sharepoint connector ON branch log appears
    And the sharepoint connector OFF branch does not run
