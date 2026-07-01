Feature: Deployment onboard check
  As a kairix operator
  I want to verify my deployment is correctly configured
  So that I can trust search results

  Scenario: All checks pass on a configured instance
    Given kairix is installed with valid credentials
    And documents are indexed
    When I run onboard check
    Then kairix_on_path passes
    And secrets_loaded passes
    And document_root_configured passes

  Scenario: Missing credentials are detected
    Given kairix is installed without API credentials
    When I run onboard check
    Then secrets_loaded fails with guidance

  Scenario: The agent memory writability probe passes on a writable overlay
    Given an agent is configured with a writable memory overlay
    When I check whether agent memory is writable
    Then agent_memory_writable passes

  Scenario: A read-only overlay with a writable fallback passes the deploy gate
    Given an agent is configured with a read-only overlay but a writable data-dir fallback
    When I check whether agent memory is writable
    Then agent_memory_writable passes via the data-dir fallback

  Scenario: An agent with no writable destination anywhere fails the deploy gate
    Given an agent has no writable memory destination anywhere
    When I check whether agent memory is writable
    Then agent_memory_writable fails with a fix hint
