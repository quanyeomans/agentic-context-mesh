@connector @cursor @f62
Feature: Connector cursor persistence across ticks
  As an operator running incremental syncs every 15 minutes
  I want each connector's opaque cursor token to be persisted and re-passed across ticks
  So that the connector doesn't do a full resync every tick (the v2026.5.28a1 production incident)

  @happy_path
  Scenario: Tick 1 persists the connector's opaque token, tick 2 receives it
    Given a connector "graph-style-source" whose next_cursor() returns an opaque deltaLink
    And tick 1 emits two change events with later modified_at timestamps than the deltaLink
    When the operator runs two consecutive pipeline ticks for "graph-style-source"
    Then tick 1 persists the deltaLink to connector_cursors, not any event modified_at
    And tick 2 calls list_changes with the deltaLink, not None

  Scenario: Quiet tick preserves the prior cursor when next_cursor() returns None
    Given a connector "graph-style-source" with a prior cursor persisted from a previous tick
    And the next tick emits zero events and the connector's next_cursor() returns None
    When the operator runs the next pipeline tick for "graph-style-source"
    Then the persisted cursor still equals the prior cursor
    And the orchestrator did not clobber the cursor row with None

  Scenario: Quiet tick advances cursor when the connector reports a new token
    Given a connector "graph-style-source" with a prior cursor persisted from a previous tick
    And the next tick emits zero events but the connector's next_cursor() advances
    When the operator runs the next pipeline tick for "graph-style-source"
    Then the persisted cursor equals the advanced token from this tick
