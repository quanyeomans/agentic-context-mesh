@feature_flag @connector_slack
Feature: connector_slack feature flag gates Wave E per-channel behaviour
  As an operator running kairix against a Slack workspace
  I want each member channel to optionally become its own Container
  with its own per-channel ts cursor and its own hierarchy emission
  So that I can sync different channels at different cadences and isolate
  cursor state per channel via the topology collection mapping
  while still being able to roll back to the legacy single-root shape.

  Wave E is the per-connector pilot for multi-container behaviour. The
  flag defaults OFF so existing operators see bit-for-bit current shim
  behaviour (one root WORKSPACE hierarchy node, list_changes_for_container
  delegating to the legacy single-cursor list_changes path). When ON, the
  connector emits one Container per member channel and emits one CHANNEL
  hierarchy node per channel under a synthetic root parent-before-child
  per F58. See docs/architecture/connector-scope-topology/connector-design-specs/slack.md
  for the Wave E specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-root hierarchy and the single-cursor list_changes path
    Given a slack connector wired to a stubbed workspace with two public channels: C-OFF-ALPHA, C-OFF-BETA
    And the operator has the connector-slack flag set to false
    When the operator calls load_hierarchy on the slack connector
    Then exactly one slack WORKSPACE node is emitted with raw_parent_id None
    When the operator calls list_changes_for_container on the slack connector with a channel container scoping to C-OFF-ALPHA
    Then the legacy single-cursor slack list_changes branch is observed

  @happy_path @on
  Scenario: Flag ON emits one Container per member channel and walks the hierarchy parent-before-child
    Given a slack connector wired to a stubbed workspace with two public channels: C-ON-ALPHA, C-ON-BETA
    And the operator has the connector-slack flag set to true
    When the operator calls iter_containers on the slack connector
    Then two slack Containers are emitted, one per member channel
    And every slack Container carries access_state ACCESSIBLE and an unset cursor_token
    When the operator calls load_hierarchy on the slack connector
    Then multiple slack hierarchy nodes are emitted parent-before-child for every channel
    When the operator calls list_changes_for_container on the slack connector with a channel container scoping to C-ON-ALPHA
    Then only slack change events from the C-ON-ALPHA channel are emitted
