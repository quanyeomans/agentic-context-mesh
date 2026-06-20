Feature: Recommend — an agent finds the right tool for a task
  As an AI agent using kairix
  I want to describe a task and get back the tool or skill that fits it
  So that I can pick the right capability without knowing the whole toolset by heart

  The recommender ranks kairix tools and local skills against a task
  description. It is gated by the recommender feature flag: when the flag
  is on it returns a ranked list; when off it tells the operator how to
  turn it on.

  Scenario: A described task returns the matching tool with a ready-to-call invocation
    Given the recommender is turned on
    And the team's toolset includes a way to check content for conflicts
    When the agent asks which tool fits "I need to check this against what we already know"
    Then the response ranks the conflict-checking tool first
    And the response includes a ready-to-call invocation for it
    And the recommend response reports no error

  Scenario: The recommender tells the operator how to turn it on when it is off
    Given the recommender is turned off
    When the agent asks which tool fits "find what we decided about the rollout"
    Then the recommend response says the recommender is disabled
