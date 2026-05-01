Feature: Search intent routing
  As an agent calling kairix search
  I want queries routed to the correct search intent
  So that results are relevant to my question

  Background:
    Given the kairix intent classifier is available

  Scenario Outline: Intent is classified correctly for canonical queries
    When I classify the query "<query>"
    Then the intent is "<intent>"

    Examples:
      | query                                    | intent     |
      | what happened last week                  | temporal   |
      | what did we complete yesterday           | temporal   |
      | tell me about OpenClaw                   | entity     |
      | who is Alice Smith                       | entity     |
      | how do I run the embedding pipeline      | procedural |
      | steps to restart the service             | procedural |
      | FEAT-081 implementation status           | keyword    |
      | infrastructure cost optimisation strategy | semantic   |
      | connection between OpenClaw and Avanade  | multi_hop  |

  Scenario: Search never raises on empty input
    When I classify the query ""
    Then no exception is raised
    And the intent is a valid QueryIntent

  Scenario: Search never raises on garbage input
    When I classify the query "!@#$%^&*()"
    Then no exception is raised
    And the intent is a valid QueryIntent

  Scenario: Temporal intent takes priority over entity
    When I classify the query "what did OpenClaw do last week"
    Then the intent is "temporal"

  Scenario: Multi-hop intent takes priority over entity
    When I classify the query "connection between Alice Smith and Avanade"
    Then the intent is "multi_hop"
