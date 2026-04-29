Feature: Agents can verify facts before writing them
  As an AI agent about to save new information to the knowledge base
  I need to check whether it conflicts with what's already known
  So that I don't introduce contradictions into the user's documents

  Scenario: Agent verifies a non-conflicting fact and gets the all-clear
    Given the search returns no contradicting documents
    When the agent calls tool_contradict with content "the sky is blue"
    Then the contradict response has_contradictions is false
    And the contradict response error is empty

  Scenario: Agent detects a conflict and gets an explanation
    Given the search finds a contradicting document
    When the agent calls tool_contradict with content "architecture uses monolith"
    Then the contradict response has_contradictions is true
    And the contradict response contains at least one contradiction with a reason

  Scenario: Agent gets a safe response even when the system has issues
    Given the search raises an error
    When the agent calls tool_contradict with content "anything"
    Then no contradict exception was raised
    And the contradict response is a valid dict
