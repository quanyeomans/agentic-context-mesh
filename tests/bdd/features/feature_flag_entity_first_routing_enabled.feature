@feature_flag @entity_first_routing_enabled
Feature: Operator toggles the entity-first-routing feature flag
  As an operator whose knowledge store has indexed entity summaries
  I want entity-named questions to surface the matching entity summary first
  So that "tell me about X" answers lead with what we already know about X

  @happy_path @on
  Scenario: Flag ON routes the entity summary above a plain note
    Given the operator has the entity-first-routing flag set to true
    When an entity-intent search ranks an entity summary against a plain note
    Then the entity summary is lifted above the plain note

  @off
  Scenario: Flag OFF leaves ranking unchanged
    Given the operator has the entity-first-routing flag set to false
    When an entity-intent search ranks an entity summary against a plain note
    Then the entity summary keeps its original score

  @on
  Scenario: Flag ON does not route for a non-entity question
    Given the operator has the entity-first-routing flag set to true
    When a keyword search ranks an entity summary against a plain note
    Then the entity summary keeps its original score
