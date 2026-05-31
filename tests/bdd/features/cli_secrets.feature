Feature: kairix secrets — canonical credential naming surface
  As a kairix operator preparing a deployment
  I want a single command that confirms every credential resolves
  And a second command that prints the legacy-to-canonical migration table
  So that I can confirm secret wiring before the first ingest and bulk-load my KV cleanly

  Scenario: Verify reports every alias as resolvable when the loader has values
    Given the kairix secrets loader resolves every registered alias
    When the operator runs the kairix secrets verify command
    Then the kairix secrets stdout marks every row as present
    And the kairix secrets command exits with code 0

  Scenario: Verify exits non-zero when at least one secret is missing
    Given the kairix secrets loader is missing one required alias
    When the operator runs the kairix secrets verify command
    Then the kairix secrets stdout marks one row as MISSING
    And the kairix secrets command exits with code 1

  Scenario: Migrate-list emits the legacy-to-canonical mapping as TSV
    When the operator runs the kairix secrets migrate-list command
    Then the kairix secrets stdout includes the TSV header
    And the kairix secrets stdout includes at least one mapping row
    And the kairix secrets command exits with code 0
