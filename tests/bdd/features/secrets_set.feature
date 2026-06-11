Feature: kairix secrets set — persist a credential under its canonical name
  As a kairix operator onboarding a new deployment
  I want to store a credential with one command
  So that every later kairix command resolves it without hand-editing env files

  Scenario: Storing a credential reports the destination and the next step
    Given an empty operator secrets bundle
    When the operator stores a value under "kairix-provider-llm-api-key"
    Then the secrets set output names the credential and the bundle file
    And the stored value is saved in the bundle file
    And the secrets set command exits with code 0

  Scenario: The stored value never appears in the command output
    Given an empty operator secrets bundle
    When the operator stores a value under "kairix-provider-llm-api-key"
    Then the secrets set output does not contain the stored value

  Scenario: A non-canonical name is rejected with corrective examples
    Given an empty operator secrets bundle
    When the operator stores a value under "MY_API_KEY"
    Then the secrets set output suggests two canonical example names
    And the secrets set command exits with code 2
    And nothing is written to the bundle file
