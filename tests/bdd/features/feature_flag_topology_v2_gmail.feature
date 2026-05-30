@feature_flag @topology_v2_gmail
Feature: topology_v2_gmail feature flag gates Wave E per-mailbox behaviour for Gmail
  As an operator running kairix against a Google Workspace mailbox
  I want the Gmail mailbox to optionally become its own Container
  with its own Gmail History API cursor
  So that I can sync per-mailbox at independent cadence and isolate
  cursor state via the topology v2 collection mapping
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector pilot for multi-container behaviour. The
  flag defaults OFF so existing operators see bit-for-bit current
  shim behaviour (list_changes_for_container delegating to the legacy
  single-cursor list_changes; one root FOLDER node). When ON, the
  connector drives list_changes_for_container against the container's
  own cursor and records a per-mailbox historyId via
  next_cursor_for_container. See
  docs/architecture/connector-scope-topology/ADR.md for the Wave E
  specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path
    Given a gmail connector wired to a stubbed gmail History API
    And the operator has the topology-v2-gmail flag set to false
    When the operator calls list_changes_for_container on the gmail connector
    Then the legacy single-cursor gmail list_changes branch is observed
    And the gmail per-container cursor map remains empty

  @happy_path @on
  Scenario: Flag ON drains the History API against the container cursor
    Given a gmail connector wired to a stubbed gmail History API
    And the operator has the topology-v2-gmail flag set to true
    When the operator calls list_changes_for_container on the gmail connector
    Then the gmail per-container cursor map carries one entry for the mailbox
    And the legacy connector-wide gmail next_cursor remains unset
