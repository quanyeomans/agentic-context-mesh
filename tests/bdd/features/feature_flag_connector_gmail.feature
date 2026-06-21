@feature_flag @connector_gmail
Feature: Operator toggles the Gmail connector feature flag
  As an operator running a kairix container
  I want to choose whether the Gmail connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  gmail entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  gmail entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the Gmail connector is gated off, the sibling still runs
    Given the operator has the gmail connector flag set to false
    And a flagless sibling connector with two notes is configured alongside gmail
    When the worker connector sync tick runs for gmail
    Then the gmail connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for gmail

  @happy_path @on
  Scenario: Flag ON - the Gmail connector is let through the gate
    Given the operator has the gmail connector flag set to true
    And a flagless sibling connector with two notes is configured alongside gmail
    When the worker connector sync tick runs for gmail
    Then the gmail connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for gmail
