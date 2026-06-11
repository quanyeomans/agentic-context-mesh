Feature: Remember — an agent saves a memory it can find again
  As an AI agent using kairix as my memory provider
  I want to save what I just learned with one command
  So that my future sessions can find it by searching

  Scenario: A configured agent saves a decision and gets back where it landed
    Given agent-alpha is declared in the team's agent configuration
    When agent-alpha remembers the decision "decided: adopt the new release checklist"
    Then the memory is saved as a dated file under agent-alpha's memory area
    And the saved memory is reported as a decision
    And the remember response reports no error

  Scenario: A built-in agent keeps working without any configuration
    Given no agent configuration exists
    When agent builder remembers the decision "decided: keep the legacy team names working"
    Then the memory is saved as a dated file under builder's memory area
    And the remember response reports no error

  Scenario: An unknown agent is told how to get configured
    Given agent-alpha is declared in the team's agent configuration
    When agent-omega tries to remember "a stray thought"
    Then the remember response is an error naming agent-omega
    And the error tells the operator to add the agent to the configuration
