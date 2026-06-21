@feature_flag @connector_sharepoint
Feature: Operator toggles the SharePoint connector feature flag
  As an operator running a kairix container
  I want to choose whether the SharePoint connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline → connector_enabled). When OFF the
  sharepoint entry is skipped before plugin resolution; a flagless
  sibling connector in the same tick still runs. When ON the gate lets
  the sharepoint entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md §7.

  @happy_path @off
  Scenario: Flag OFF — the SharePoint connector is gated off, the sibling still runs
    Given the operator has the sharepoint connector flag set to false
    And a flagless sibling connector with two notes is configured alongside sharepoint
    When the worker connector sync tick runs
    Then the sharepoint connector is gated off in the loop
    And the flagless sibling connector still syncs its notes

  @happy_path @on
  Scenario: Flag ON — the SharePoint connector is let through the gate
    Given the operator has the sharepoint connector flag set to true
    And a flagless sibling connector with two notes is configured alongside sharepoint
    When the worker connector sync tick runs
    Then the sharepoint connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes
