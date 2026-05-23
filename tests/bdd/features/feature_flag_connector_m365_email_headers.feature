@feature_flag @connector_m365_email_headers
Feature: Operator toggles the m365-email-headers connector feature flag
  As an operator running a kairix engagement container
  I want to choose whether the M365 email-headers connector is enabled
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  The flag gates the connector at the dispatch boundary. When OFF the
  connector slot is a no-op (zero counters); when ON the standard
  connector pipeline resolves the m365_email_headers plugin via its
  entry-point and runs one sync tick. See
  docs/architecture/feature-flag-architecture.md §7.

  @happy_path @off
  Scenario: Flag OFF — the M365 connector slot is a no-op
    Given the operator has the m365-email-headers connector flag set to false
    When the worker m365 connector sync tick runs
    Then the m365 connector OFF branch log appears
    And the m365 connector ON branch does not run

  @happy_path @on
  Scenario: Flag ON — the M365 connector pipeline runs
    Given the operator has the m365-email-headers connector flag set to true
    When the worker m365 connector sync tick runs
    Then the m365 connector ON branch log appears
    And the m365 connector OFF branch does not run
