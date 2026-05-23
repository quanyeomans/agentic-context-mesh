@feature_flag @topology_v2_config
Feature: topology_v2_config feature flag gates Wave D operator-config promotion
  As an operator landing the topology v2 connector / collection / scope
  topology migration (per docs/architecture/connector-scope-topology/ADR.md)
  I want the 6 Wave D operator-config blocks (connectors / credentials /
  cc_pairs / collections / scope_profiles / skills) to EXIST UNCONDITIONALLY
  in the schema + parser surface
  but the worker startup + `kairix features status` topology-v2 output
  + `kairix cc-pair` verbs to ROUTE THROUGH the Wave D surface only when
  the flag is on
  So that the operator-config promotion cutover is a separate deliberate
  action with default-safe rollback to today's legacy single-collection
  shape.

  Wave D is pure-additive: when the flag is OFF, the parser still loads
  YAML (no behaviour change for legacy configs), but the topology v2
  surface stays inert. When the flag is ON, declared cc_pairs / collections
  / scope profiles drive routing + scope enforcement + diagnostics.

  @happy_path @off
  Scenario: flag default-off — kairix features status topology v2 surface is inert
    Given the operator has the topology-v2-config flag set to false
    When the operator queries the topology-v2-config flag effective value
    Then the topology-v2-config flag is reported as effective false

  @on
  Scenario: flag effective-true — topology v2 surface is live + diagnostics surface populated
    Given the operator has the topology-v2-config flag set to true
    When the operator queries the topology-v2-config flag effective value
    Then the topology-v2-config flag is reported as effective true
