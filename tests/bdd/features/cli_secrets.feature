Feature: kairix secrets — canonical credential naming surface
  As a kairix operator preparing a deployment
  I want a single command that confirms every credential resolves
  So that I can confirm secret wiring before the first ingest

  Scenario: Verify reports every credential as resolvable when the loader has values
    Given the kairix secrets loader resolves every registered credential
    When the operator runs the kairix secrets verify command
    Then the kairix secrets stdout marks every row as present
    And the kairix secrets command exits with code 0

  Scenario: Verify exits non-zero when at least one secret is missing
    Given the kairix secrets loader is missing one required credential
    When the operator runs the kairix secrets verify command
    Then the kairix secrets stdout marks one row as MISSING
    And the kairix secrets command exits with code 1
