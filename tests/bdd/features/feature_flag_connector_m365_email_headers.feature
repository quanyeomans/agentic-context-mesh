@feature_flag @connector_m365_email_headers
Feature: Operator toggles the M365 email-headers connector feature flag
  As an operator running a kairix container
  I want to choose whether the M365 email-headers connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  m365 email-headers entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  m365 email-headers entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the M365 email-headers connector is gated off, the sibling still runs
    Given the operator has the m365 email-headers connector flag set to false
    And a flagless sibling connector with two notes is configured alongside m365 email-headers
    When the worker connector sync tick runs for m365 email-headers
    Then the m365 email-headers connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for m365 email-headers

  @happy_path @on
  Scenario: Flag ON - the M365 email-headers connector is let through the gate
    Given the operator has the m365 email-headers connector flag set to true
    And a flagless sibling connector with two notes is configured alongside m365 email-headers
    When the worker connector sync tick runs for m365 email-headers
    Then the m365 email-headers connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for m365 email-headers
