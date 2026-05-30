@feature_flag @topology_v2_google_calendar
Feature: topology_v2_google_calendar feature flag gates Google Calendar connector activation
  As an operator running kairix while the Google Workspace OAuth credentials are still being provisioned
  I want the Google Calendar connector to be inert until the topology-v2-google-calendar flag is flipped on
  So that merging the connector code is structurally a no-op for production
  and the cutover is a separate deliberate action once the credentials land in kv-tc-agents.

  The flag defaults OFF so existing operators see bit-for-bit current
  behaviour (the connector never runs even when listed in
  kairix.config.yaml). When ON, the worker's
  dispatch_google_calendar_sync routes through the standard connector
  pipeline which resolves the google_calendar plugin via its
  entry-point factory. Tracked GH #356 for KV provisioning.

  @happy_path @off
  Scenario: Flag OFF keeps the Google Calendar connector inert
    Given the operator has the topology-v2-google-calendar flag set to false
    When the operator runs the google_calendar dispatcher
    Then the dispatcher reports zero synced documents for google_calendar
    And the google_calendar off-branch noop is observed in the worker logs

  @happy_path @on
  Scenario: Flag ON routes the dispatcher through the standard connector pipeline
    Given the operator has the topology-v2-google-calendar flag set to true
    When the operator runs the google_calendar dispatcher
    Then the google_calendar on-branch run is observed in the worker logs
