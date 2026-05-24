@feature_flag @topology_v2_sharepoint
Feature: topology_v2_sharepoint feature flag gates Wave E per-drive behaviour
  As an operator running kairix against one or more SharePoint document libraries
  I want each configured drive to optionally become its own Container
  with its own Graph delta cursor and its own hierarchy emission
  So that I can add or remove individual drives without
  disturbing the resume position of the other drives
  while still being able to roll back to the legacy single-cursor shape.

  Wave E is the per-connector slice for multi-drive behaviour. The flag
  defaults OFF so existing operators see bit-for-bit current shim
  behaviour (list_changes_for_container delegating to the legacy
  single-cursor list_changes that shares one packed JSON cursor across
  every configured drive). When ON, the connector emits one Container
  per configured drive, the Graph delta query runs per-drive with the
  container's own cursor_token, load_hierarchy emits a root SITE FOLDER
  plus one DRIVE FOLDER per configured drive parent-before-child, and
  the Resolver.reindex method replays only the supplied failed item ids
  instead of re-running a delta window. See
  docs/architecture/connector-scope-topology/connector-design-specs/sharepoint.md
  for the Wave E specification.

  @happy_path @off
  Scenario: Flag OFF keeps the legacy single-cursor list_changes path
    Given a sharepoint connector configured for two drives: drive-alpha, drive-beta
    And the operator has the topology-v2-sharepoint flag set to false
    When the operator calls iter_containers on the sharepoint connector
    Then two Containers are emitted, one per configured drive
    When the operator drives list_changes_for_container against drive drive-alpha
    Then the legacy single-cursor list_changes branch is observed for sharepoint
    When the operator calls load_hierarchy on the sharepoint connector
    Then one root FOLDER node is emitted with no drive children for sharepoint

  @happy_path @on
  Scenario: Flag ON emits one Container per configured drive and isolates per-drive cursors
    Given a sharepoint connector configured for two drives: drive-alpha, drive-beta
    And the operator has the topology-v2-sharepoint flag set to true
    When the operator calls iter_containers on the sharepoint connector
    Then two Containers are emitted, one per configured drive
    And every sharepoint Container carries access_state ACCESSIBLE with no cursor_token yet
    When the operator calls load_hierarchy on the sharepoint connector
    Then FOLDER nodes are emitted parent-before-child with a SITE root and one DRIVE child per drive
    When the operator drives list_changes_for_container against drive drive-alpha
    Then the sharepoint Graph delta query targets only drive drive-alpha
    When the operator calls reindex on the sharepoint connector with failed ids item-x and item-y
    Then the sharepoint reindex emits exactly one event per supplied failed id
