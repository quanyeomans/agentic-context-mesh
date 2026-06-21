@feature_flag @connector_m365_calendar
Feature: Operator toggles the M365 calendar connector feature flag
  As an operator running a kairix container
  I want to choose whether the M365 calendar connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  m365 calendar entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  m365 calendar entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the M365 calendar connector is gated off, the sibling still runs
    Given the operator has the m365 calendar connector flag set to false
    And a flagless sibling connector with two notes is configured alongside m365 calendar
    When the worker connector sync tick runs for m365 calendar
    Then the m365 calendar connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for m365 calendar

  @happy_path @on
  Scenario: Flag ON - the M365 calendar connector is let through the gate
    Given the operator has the m365 calendar connector flag set to true
    And a flagless sibling connector with two notes is configured alongside m365 calendar
    When the worker connector sync tick runs for m365 calendar
    Then the m365 calendar connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for m365 calendar
