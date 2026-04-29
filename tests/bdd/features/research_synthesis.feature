Feature: Research always gives the best answer it can
  As an AI agent researching a question for a user
  I need to always get a useful answer — even when the knowledge base only partially covers the topic
  So that the user gets something helpful rather than "I couldn't find anything"

  Scenario: Agent gets a best-effort answer when evidence is incomplete
    Given the research finds documents but confidence is low
    When the agent completes research
    Then the research state has a non-empty synthesis
    And the research state confidence is greater than zero
