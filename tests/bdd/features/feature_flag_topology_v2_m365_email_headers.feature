@feature_flag @topology_v2_m365_email_headers
Feature: topology_v2_m365_email_headers feature flag gates Wave E per-mailbox behaviour
  As an operator running kairix against one or more Microsoft 365 mailboxes
  I want each configured mailbox to optionally become its own Container
  with its own Graph delta cursor and its own hierarchy emission
  So that I can sync different mailboxes at different cadences and isolate
  cursor state per mailbox via the topology v2 collection mapping
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector pilot for multi-container behaviour. The
  flag defaults OFF so existing operators see bit-for-bit current
  shim behaviour (one root FOLDER node, list_changes_for_container
  delegating to the legacy single-cursor list_changes). When ON, the
  connector emits one Container per configured mailbox and emits one
  FOLDER node per mailbox under a synthetic root parent-before-child
  per F58. See docs/architecture/connector-scope-topology/ADR.md for
  the Wave E specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path and a single root FOLDER node
    Given an m365 email-headers connector with three configured mailboxes: agent-alpha@example.com, agent-beta@example.com, agent-gamma@example.com
    And the operator has the topology-v2-m365-email-headers flag set to false
    When the operator calls load_hierarchy on the m365 email-headers connector
    Then exactly one m365 FOLDER node is emitted with raw_parent_id None
    When the operator calls list_changes_for_container on the m365 email-headers connector with a mailbox container scoping to agent-alpha@example.com
    Then the legacy single-cursor m365 list_changes branch is observed

  @happy_path @on
  Scenario: Flag ON emits one Container per configured mailbox and walks the hierarchy parent-before-child
    Given an m365 email-headers connector with three configured mailboxes: agent-alpha@example.com, agent-beta@example.com, agent-gamma@example.com
    And the operator has the topology-v2-m365-email-headers flag set to true
    When the operator calls iter_containers on the m365 email-headers connector
    Then three m365 Containers are emitted, one per configured mailbox
    And every m365 Container carries access_state ACCESSIBLE and an unset cursor_token
    When the operator calls load_hierarchy on the m365 email-headers connector
    Then multiple m365 FOLDER nodes are emitted parent-before-child for every mailbox
    When the operator calls list_changes_for_container on the m365 email-headers connector with a mailbox container scoping to agent-alpha@example.com
    Then only m365 change events from the agent-alpha@example.com mailbox are emitted
