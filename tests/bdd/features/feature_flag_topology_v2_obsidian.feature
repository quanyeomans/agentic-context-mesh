@feature_flag @topology_v2_obsidian
Feature: topology_v2_obsidian feature flag gates Wave E per-container behaviour
  As an operator running kairix against an Obsidian vault
  I want each top-level folder of the vault to optionally become its own
  Container with its own delta cursor and its own hierarchy emission
  So that I can sync different folders at different cadences and scope
  retrieval per-folder via the topology v2 collection mapping
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector pilot for multi-container behaviour. The
  flag defaults OFF so existing operators see bit-for-bit current
  shim behaviour (one root FOLDER node, list_changes_for_container
  delegating to the legacy single-cursor list_changes). When ON, the
  connector emits one Container per top-level vault folder and walks
  the filesystem emitting one FOLDER node per directory parent-before-
  child per F58. See docs/architecture/connector-scope-topology/ADR.md
  for the Wave E specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path and a single root FOLDER node
    Given a vault with three top-level folders: 00-Home, 01-Projects, 02-Areas
    And the operator has the topology-v2-obsidian flag set to false
    When the operator calls load_hierarchy on the obsidian connector
    Then exactly one FOLDER node is emitted with raw_parent_id None
    When the operator calls list_changes_for_container with a Container scoping to 01-Projects
    Then the legacy single-cursor list_changes branch is observed

  @happy_path @on
  Scenario: Flag ON emits one Container per top-level folder and walks the hierarchy parent-before-child
    Given a vault with three top-level folders: 00-Home, 01-Projects, 02-Areas
    And the operator has the topology-v2-obsidian flag set to true
    When the operator calls iter_containers on the obsidian connector
    Then three Containers are emitted, one per top-level folder
    And every Container carries access_state ACCESSIBLE and an unset cursor_token
    When the operator calls load_hierarchy on the obsidian connector
    Then multiple FOLDER nodes are emitted parent-before-child for every directory
    When the operator calls list_changes_for_container with a Container scoping to 01-Projects
    Then only change events under the 01-Projects subtree are emitted
