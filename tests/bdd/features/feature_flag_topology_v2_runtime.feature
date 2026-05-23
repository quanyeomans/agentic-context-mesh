@feature_flag @topology_v2_runtime
Feature: topology_v2_runtime feature flag gates Wave C behaviour
  As an operator landing the topology v2 connector / collection / scope
  topology migration (per docs/architecture/connector-scope-topology/ADR.md)
  I want Wave C runtime components (CollectionRouter, ChunkerRegistry,
  ScopeProfileResolver, ResultEnvelope) to EXIST UNCONDITIONALLY
  but the worker's connector-sync chunk-write dispatch to ROUTE THROUGH
  CollectionRouter only when the flag is on
  So that the per-folder routing + chunker-registry dispatch + HierarchyNode
  emission cutover is a separate deliberate action with default-safe
  rollback to today's single-collection behaviour.

  Wave C is pure-additive at runtime: when the flag is OFF, every chunk
  write lands in the legacy single-collection writer (bit-for-bit parity
  with today). When the flag is ON, the worker looks up cc_pair_id for
  each connector entry and routes chunks through CollectionRouter.

  @happy_path @off
  Scenario: flag default-off keeps the single-collection writer dispatch active
    Given the operator has the topology-v2-runtime flag set to false
    When the worker resolves the chunk-writer for a connector entry
    Then the legacy single-collection writer is selected
    And no CollectionRouter is constructed
    And the chunker registry fallback is unchanged

  @on
  Scenario: flag effective-true routes chunk writes through CollectionRouter
    Given the operator has the topology-v2-runtime flag set to true
    When the worker resolves the chunk-writer for a connector entry whose cc_pair has mappings
    Then a CollectionRouter is constructed for the cc_pair
    And the topology-v2-runtime flag activation appears in the observability log
    And subsequent topology-v2-runtime status queries report source=config effective=true
