@feature_flag @topology_v2_m365_calendar
Feature: topology_v2_m365_calendar feature flag gates Wave E per-calendar behaviour
  As an operator running kairix against one or more Microsoft 365 calendars
  I want each configured calendar to optionally become its own Container
  with its own Graph delta cursor and its own hierarchy emission
  So that I can add or remove individual user mailboxes without
  disturbing the resume position of the other calendars
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector slice for multi-calendar behaviour. The
  flag defaults OFF so existing operators see bit-for-bit current shim
  behaviour (list_changes_for_container delegating to the legacy
  single-cursor list_changes that shares one deltaLink across every
  configured calendar). When ON, the connector emits one Container per
  configured UPN and the Graph delta query runs per-calendar with the
  container's own cursor_token. load_hierarchy emits a root FOLDER node
  plus one child per configured calendar on both branches. See
  docs/architecture/connector-scope-topology/ADR.md for the Wave E
  specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path
    Given an m365_calendar connector configured for two mailboxes: alice@example.com, bob@example.com
    And the operator has the topology-v2-m365-calendar flag set to false
    When the operator calls iter_containers on the m365_calendar connector
    Then two Containers are emitted, one per configured calendar
    When the operator drives list_changes_for_container against the calendar for mailbox alice@example.com
    Then the legacy single-cursor list_changes branch is observed for m365_calendar

  @happy_path @on
  Scenario: Flag ON emits one Container per configured calendar and isolates per-calendar cursors
    Given an m365_calendar connector configured for two mailboxes: alice@example.com, bob@example.com
    And the operator has the topology-v2-m365-calendar flag set to true
    When the operator calls iter_containers on the m365_calendar connector
    Then two Containers are emitted, one per configured calendar
    And every calendar Container carries access_state ACCESSIBLE with no cursor_token yet
    When the operator calls load_hierarchy on the m365_calendar connector
    Then FOLDER nodes are emitted parent-before-child with a root and one child per calendar
    When the operator drives list_changes_for_container against the calendar for mailbox alice@example.com
    Then the Graph delta query targets only alice@example.com
