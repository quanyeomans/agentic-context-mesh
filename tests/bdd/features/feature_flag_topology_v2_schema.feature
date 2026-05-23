@feature_flag @topology_v2_schema
Feature: topology_v2_schema feature flag gates Wave A behaviour
  As an operator landing the topology v2 connector / collection / scope
  topology migration (per docs/architecture/connector-scope-topology/ADR.md)
  I want the Wave A schema additions (12 new tables + new dataclasses) to
  exist UNCONDITIONALLY (CREATE IF NOT EXISTS is safe on every restart)
  but writes into those tables to be GATED by the flag
  So that Wave B Protocol shims, Wave C runtime, etc. each layer their
  behaviour behind the same flag with default-safe rollback.

  Wave A is pure-additive: empty tables + new dataclass definitions.
  Default-off means no production code path populates the new tables
  yet — the flag's effective=true is the precondition for any Wave B+
  write to fire.

  @happy_path @off
  Scenario: flag default-off means no production code path populates topology v2 tables
    Given the operator has the topology-v2-schema flag set to false
    When the worker boots and runs a connector sync cycle
    Then the topology_v2 tables exist (CREATE IF NOT EXISTS is unconditional)
    And the topology_v2 tables are empty (no Wave B+ write path is active)
    And no production behaviour observable to a search caller has changed

  @on
  Scenario: flag effective-true unlocks Wave B+ write paths
    Given the operator has the topology-v2-schema flag set to true
    When a Wave B+ code path attempts to populate a topology_v2 table
    Then the write succeeds
    And the flag activation appears in the feature-flag observability log
    And subsequent flag-status queries report source=config effective=true
