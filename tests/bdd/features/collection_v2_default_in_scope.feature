Feature: topology_v2 collection model — default in-scope and opt-in retrieval
  As a kairix operator
  I want default search to return a broad, useful superset of in-default collections
  And opt-in collections (like the reference library) to be reachable by explicit name
  So that agents get useful results without leaking specialised or other-agents' content

  Background:
    Given the operator has migrated to the topology v2 collection model

  Scenario: Agent's default search returns the broad superset
    Given the topology_v2_default_in_scope flag is ON
    And the operator has configured 7 in-default collections and 1 opt-in collection
    And agent "shape" has a scope_profile covering all 8 collections
    When agent "shape" issues a search with no collections specified
    Then the search returns hits from all 7 in-default collections
    And the search does not return hits from the opt-in collection

  Scenario: Agent can opt-in to a non-default collection explicitly
    Given the topology_v2_default_in_scope flag is ON
    And agent "shape" has reflib in scope with default_in_scope=false
    When agent "shape" issues a search with collections=["reflib"]
    Then the search returns hits from reflib only

  Scenario: Agent cannot retrieve another agent's memory
    Given the topology_v2_default_in_scope flag is ON
    And agent "shape" does not have builder-memory in scope
    When agent "shape" issues a search with collections=["builder-memory"]
    Then the search returns no results
    And the operator-facing error message contains "fix:" and "next:" markers

  Scenario: Feature flag OFF preserves legacy resolver behaviour
    Given the topology_v2_default_in_scope flag is OFF
    And the legacy collections block declares 5 in-default collections
    When agent "shape" issues a search with no collections specified
    Then the search routes via the legacy default collection resolver
    And returns hits from the 5 in-default legacy collections

  Scenario: Wildcard applies_to expands to every registered agent
    Given a scope_profile with applies_to=["*"]
    And 6 registered agents in the agents block
    When the config loader materialises the scope_profiles
    Then every agent has the wildcard profile's collections in their scope
