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
