@feature_flag @topology_v2_dex_crm
Feature: topology_v2_dex_crm feature flag gates Wave E per-container behaviour
  As an operator running kairix against a Dex CRM tenant
  I want the Dex connector to optionally surface a per-container cursor and
  a real per-entity-type hierarchy emission
  So that the cc_pair cursor threads through Wave C's CollectionRouter and
  the search layer surfaces a Dex CRM / Person / Organisation / Relationship
  folder breadcrumb
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector pilot for multi-container behaviour. The
  flag defaults OFF so existing operators see bit-for-bit current
  shim behaviour (one root FOLDER node, list_changes_for_container
  delegating to the legacy single-cursor list_changes). When ON, the
  connector emits one Container per tenant (the Dex API has no
  per-organisation delta) and walks an entity-type hierarchy emitting
  one FOLDER node per top-level kind parent-before-child per F58. See
  docs/architecture/connector-scope-topology/ADR.md for the Wave E
  specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path and a single dex root FOLDER node
    Given a configured dex_crm connector with a scripted Dex API
    And the operator has the topology-v2-dex-crm flag set to false
    When the operator calls load_hierarchy on the dex_crm connector
    Then exactly one dex root FOLDER node is emitted with raw_parent_id None
    When the operator calls list_changes_for_container with the dex tenant Container
    Then the dex_crm legacy single-cursor list_changes branch is observed

  @happy_path @on
  Scenario: Flag ON emits one tenant Container and walks the dex hierarchy parent-before-child
    Given a configured dex_crm connector with a scripted Dex API
    And the operator has the topology-v2-dex-crm flag set to true
    When the operator calls iter_containers on the dex_crm connector
    Then one dex Container is emitted for the tenant
    And the dex Container carries access_state ACCESSIBLE and an unset cursor_token
    When the operator calls load_hierarchy on the dex_crm connector
    Then four FOLDER nodes are emitted parent-before-child for the dex hierarchy
    When the operator calls list_changes_for_container with the dex tenant Container
    Then dex change events are emitted via the per-container cursor path
