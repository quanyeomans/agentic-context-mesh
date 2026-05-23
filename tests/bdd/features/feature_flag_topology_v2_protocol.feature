@feature_flag @topology_v2_protocol
Feature: topology_v2_protocol feature flag gates Wave B behaviour
  As an operator landing the topology v2 connector / collection / scope
  topology migration (per docs/architecture/connector-scope-topology/ADR.md)
  I want the Wave B capability-Protocol shims (PollConnector,
  CheckpointedConnector, SlimConnector, etc.) to exist UNCONDITIONALLY
  so every shipped connector still satisfies the new shapes
  but the worker's sync-dispatch routing through those capabilities to
  be GATED by the flag
  So that Wave C runtime activation is a separate deliberate step with
  default-safe rollback.

  Wave B is pure-additive: 9 new capability Protocols + default-impl
  shims on the 4 shipped connectors. Default-off means the worker's
  legacy single-cursor SourceConnector dispatch is unchanged — the
  flag's effective=true is the precondition for Wave C runtime
  routing.

  @happy_path @off
  Scenario: flag default-off keeps the legacy single-cursor dispatch path active
    Given the operator has the topology-v2-protocol flag set to false
    When the worker boots and dispatches a connector sync cycle
    Then the legacy single-cursor SourceConnector path runs
    And no capability-mix-in routing is observed
    And the connector still satisfies the capability Protocols (shims are present)

  @on
  Scenario: flag effective-true unlocks the capability-Protocol dispatch path
    Given the operator has the topology-v2-protocol flag set to true
    When a Wave C+ code path inspects connector capabilities
    Then the connector is reported as satisfying PollConnector
    And the topology-v2-protocol flag activation appears in the observability log
    And subsequent topology-v2-protocol status queries report source=config effective=true
