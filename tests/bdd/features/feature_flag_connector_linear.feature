@feature_flag @connector_linear
Feature: Operator toggles the Linear connector feature flag
  As an operator running a kairix container
  I want to choose whether the Linear connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  linear entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  linear entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the Linear connector is gated off, the sibling still runs
    Given the operator has the linear connector flag set to false
    And a flagless sibling connector with two notes is configured alongside linear
    When the worker connector sync tick runs for linear
    Then the linear connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for linear

  @happy_path @on
  Scenario: Flag ON - the Linear connector is let through the gate
    Given the operator has the linear connector flag set to true
    And a flagless sibling connector with two notes is configured alongside linear
    When the worker connector sync tick runs for linear
    Then the linear connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for linear
