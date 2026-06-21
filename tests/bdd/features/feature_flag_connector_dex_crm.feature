@feature_flag @connector_dex_crm
Feature: Operator toggles the Dex CRM connector feature flag
  As an operator running a kairix container
  I want to choose whether the Dex CRM connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  dex crm entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  dex crm entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the Dex CRM connector is gated off, the sibling still runs
    Given the operator has the dex crm connector flag set to false
    And a flagless sibling connector with two notes is configured alongside dex crm
    When the worker connector sync tick runs for dex crm
    Then the dex crm connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for dex crm

  @happy_path @on
  Scenario: Flag ON - the Dex CRM connector is let through the gate
    Given the operator has the dex crm connector flag set to true
    And a flagless sibling connector with two notes is configured alongside dex crm
    When the worker connector sync tick runs for dex crm
    Then the dex crm connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for dex crm
