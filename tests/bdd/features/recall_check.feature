Feature: Adaptive recall quality check
  As a kairix operator
  I want the recall check to detect embedding quality degradation
  So that I catch silent index corruption before it affects search

  Scenario: Adaptive queries are generated from indexed documents
    Given an index with titled documents
    When the recall check builds adaptive queries
    Then at least 3 recall queries are generated
    And each query has an id, query text, and expected fragment

  Scenario: Default recall queries are used when no documents exist
    Given an empty search index
    When the recall check builds queries
    Then the default recall queries are used
    And at least 5 queries are returned

  Scenario: Degradation threshold triggers alert
    Given a previous recall score of 0.90
    And a current recall score of 0.70
    When the recall gate compares scores
    Then degradation is detected
