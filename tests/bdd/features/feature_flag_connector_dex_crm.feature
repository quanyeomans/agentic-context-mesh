@feature_flag @connector_dex_crm
Feature: Operator toggles the connector-dex-crm feature flag
  As an operator running a kairix engagement container against a Dex CRM
  I want to enable the Dex connector before it starts pulling Person and Org records
  So that I can opt in deliberately and roll back safely if the integration misbehaves

  The flag defaults off at the introduce stage. With the flag off the
  Dex connector never polls the API; with the flag on the worker drives
  it through the standard SourceConnector list_changes path. See
  docs/architecture/feature-flag-architecture.md Wave 5.

  @happy_path @off
  Scenario: Flag OFF — the Dex CRM connector does not poll the Dex API
    Given the operator has the connector-dex-crm flag set to false
    When the worker dex crm sync tick runs
    Then the dex crm connector branch is skipped
    And no api call is made to the dex crm endpoint

  @happy_path @on
  Scenario: Flag ON — the Dex CRM connector polls the Dex API
    Given the operator has the connector-dex-crm flag set to true
    When the worker dex crm sync tick runs
    Then the dex crm connector branch performs the sync pass
    And the dex crm connector lists changes since the cursor
