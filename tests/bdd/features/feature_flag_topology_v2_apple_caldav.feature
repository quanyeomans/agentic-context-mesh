@feature_flag @topology_v2_apple_caldav
Feature: topology_v2_apple_caldav feature flag gates Wave E per-calendar behaviour
  As an operator running kairix against an Apple iCloud account with multiple calendars
  I want each iCloud calendar to optionally become its own Container
  with its own CalDAV sync token and its own hierarchy emission
  So that I can add or remove individual calendars without
  disturbing the resume position of the others
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector slice for the apple_caldav connector.
  The flag defaults OFF so existing operators see bit-for-bit current
  shim behaviour (list_changes_for_container delegating to the legacy
  single-cursor list_changes that folds every calendar's token into
  one composite cursor). When ON, the connector emits one Container
  per discovered calendar and the CalDAV <sync-collection> REPORT
  runs per-calendar with the container's own cursor_token.
  load_hierarchy emits a root FOLDER node plus one child per
  discovered calendar on both branches. See
  docs/architecture/connector-scope-topology/ADR.md for the Wave E
  specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path
    Given an apple_caldav connector configured to discover two calendars
    And the operator has the topology-v2-apple-caldav flag set to false
    When the operator calls iter_containers on the apple_caldav connector
    Then two apple_caldav Containers are emitted, one per discovered calendar
    When the operator drives list_changes_for_container against the personal calendar
    Then the legacy single-cursor list_changes branch is observed for apple_caldav

  @happy_path @on
  Scenario: Flag ON emits one Container per discovered calendar and isolates per-calendar cursors
    Given an apple_caldav connector configured to discover two calendars
    And the operator has the topology-v2-apple-caldav flag set to true
    When the operator calls iter_containers on the apple_caldav connector
    Then two apple_caldav Containers are emitted, one per discovered calendar
    And every apple_caldav Container carries access_state ACCESSIBLE with no cursor_token yet
    When the operator calls load_hierarchy on the apple_caldav connector
    Then apple_caldav FOLDER nodes are emitted parent-before-child with a root and one child per calendar
    When the operator drives list_changes_for_container against the personal calendar
    Then the CalDAV sync REPORT targets only the personal calendar
