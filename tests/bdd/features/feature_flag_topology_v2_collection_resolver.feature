@feature_flag @topology_v2_collection_resolver
Feature: topology_v2_collection_resolver feature flag gates the superset default
  As an operator landing the topology v2 ScopeProfileResolver wiring
  (per docs/architecture/connector-scope-topology/ADR.md §6)
  I want the search pipeline's CollectionResolver to ROUTE THROUGH
  the topology v2 scope-profile superset adapter only when the flag is ON
  So that the legacy DefaultCollectionResolver path stays the default-safe
  rollback target while the v2 superset behaviour soaks against the
  dogfood deployment.

  When the flag is OFF, the legacy resolver reads collections.shared[].in_default
  from kairix.config.yaml exactly as it does today (bit-for-bit parity).
  When the flag is ON, calling kairix without specifying collections returns
  the superset of every collection the agent's scope_profile grants read
  access to.

  @happy_path @off
  Scenario: flag default-off keeps the legacy DefaultCollectionResolver active
    Given the operator has the topology-v2-collection-resolver flag set to false
    When the factory builds the collection resolver
    Then the legacy default collection resolver is selected
    And no topology v2 collection resolver is constructed

  @on
  Scenario: flag effective-true returns the superset of an agent's scope
    Given the operator has the topology-v2-collection-resolver flag set to true
    And the actor profile grants read access to four collections
    When the agent queries with no explicit collections
    Then the resolver returns every read-eligible collection name in the profile

  @on
  Scenario: flag effective-true validates explicit collections against the scope
    Given the operator has the topology-v2-collection-resolver flag set to true
    And the actor profile grants read access to one collection
    When the agent queries with an out-of-scope explicit collection
    Then the resolver rejects the request with an actionable error
