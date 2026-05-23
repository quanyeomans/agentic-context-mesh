@feature_flag @connector_m365_calendar
Feature: Operator toggles the connector-m365-calendar feature flag
  As an operator running a kairix engagement container
  I want to choose whether to enable the new M365 calendar connector
  So that I can validate the calendar ingest path on staging before turning it on in production and roll it back safely if needed.

  The flag is at introduce stage and defaults off. Both code paths
  stay present until the flag retires; the operator can flip the flag
  back at any time. See docs/architecture/feature-flag-architecture.md
  §7 for the cutover protocol.

  @happy_path @off
  Scenario: Flag OFF — the m365_calendar connector is not selected for sync
    Given the operator has the connector-m365-calendar flag set to false
    When the worker resolves the enabled connector set
    Then the m365_calendar connector is not in the resolved set
    And no Graph traffic is initiated for the m365_calendar connector

  @happy_path @on
  Scenario: Flag ON — the m365_calendar connector is selected for sync
    Given the operator has the connector-m365-calendar flag set to true
    When the worker resolves the enabled connector set
    Then the m365_calendar connector is in the resolved set
    And the m365_calendar connector ingest branch is ready to run
