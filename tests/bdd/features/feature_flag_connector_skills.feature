@feature_flag @connector_skills
Feature: Operator toggles the skills connector feature flag
  As an operator running a kairix container
  I want to choose whether the skills connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector inside the canonical connector-sync loop
  (run_connector_sync_pipeline -> connector_enabled). When OFF the
  skills entry is skipped before plugin resolution; a flagless sibling
  connector in the same tick still runs. When ON the gate lets the
  skills entry through to the batch runner. See
  docs/architecture/feature-flag-architecture.md S7.

  @happy_path @off
  Scenario: Flag OFF - the skills connector is gated off, the sibling still runs
    Given the operator has the skills connector flag set to false
    And a flagless sibling connector with two notes is configured alongside skills
    When the worker connector sync tick runs for skills
    Then the skills connector is gated off in the loop
    And the flagless sibling connector still syncs its notes for skills

  @happy_path @on
  Scenario: Flag ON - the skills connector is let through the gate
    Given the operator has the skills connector flag set to true
    And a flagless sibling connector with two notes is configured alongside skills
    When the worker connector sync tick runs for skills
    Then the skills connector is not gated off in the loop
    And the flagless sibling connector still syncs its notes for skills
