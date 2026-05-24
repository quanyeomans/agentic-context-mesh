@feature_flag @topology_v2_github
Feature: Operator toggles the topology v2 github pilot flag
  As an operator running the dogfood GitHub installation
  I want to choose whether the github connector emits one Container per repository
  So that I can soak the per-repo cursor isolation pattern before promoting it for every operator

  The flag gates the per-container emission boundary. When OFF the
  connector retains the Wave B shim shape (one shared cursor across
  every repo, load_hierarchy emits one root ORG node); when ON the
  connector emits one Container per installation-accessible
  repository, list_changes_for_container scopes the drain to that
  repo, and load_hierarchy walks Org then repo then top-level
  directory parent-before-child per F58. See
  docs/architecture/connector-scope-topology/connector-design-specs/github.md
  section 1 and docs/architecture/feature-flag-architecture.md section 7.

  @happy_path @off
  Scenario: Flag OFF the github connector retains the legacy single-cursor shape
    Given the operator has the topology v2 github flag set to false
    And a github connector with two seeded repositories
    When the operator calls list_changes_for_container for one repository
    Then the connector reports it took the legacy code path
    And load_hierarchy emits one root ORG node

  @happy_path @on
  Scenario: Flag ON the github connector emits per-repository containers
    Given the operator has the topology v2 github flag set to true
    And a github connector with two seeded repositories
    When the operator calls list_changes_for_container for one repository
    Then the connector reports it took the scoped code path
    And load_hierarchy emits parent before child for org then repo
