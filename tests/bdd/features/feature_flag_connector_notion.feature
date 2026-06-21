@feature_flag @connector_notion
Feature: Operator toggles the Notion connector feature flag
  As an operator running a kairix container
  I want to choose whether the Notion connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  notion entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  notion entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the Notion connector is gated off, the sibling still runs
    Given the operator has the notion connector flag set to false
    And a flagless sibling connector with two notes is configured alongside notion
    When the worker connector sync tick runs for notion
    Then the notion connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for notion

  @happy_path @on
  Scenario: Flag ON - the Notion connector is let through the gate
    Given the operator has the notion connector flag set to true
    And a flagless sibling connector with two notes is configured alongside notion
    When the worker connector sync tick runs for notion
    Then the notion connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for notion
