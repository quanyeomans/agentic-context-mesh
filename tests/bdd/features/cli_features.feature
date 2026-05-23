Feature: kairix features status — operator-facing flag introspection
  As a kairix operator running a kairix engagement container
  I want a single command that lists every registered feature flag and its effective value
  So that I can confirm what's enabled before relying on flag-gated behaviour

  Scenario: Empty registry — operator sees the friendly "no flags" line
    Given the kairix features registry is empty
    When the operator runs the kairix features status command
    Then the kairix features stdout reports no feature flags registered
    And the kairix features command exits with code 0

  Scenario: JSON output emits the canonical envelope shape
    Given the kairix features registry is empty
    When the operator runs the kairix features status command with the --json flag
    Then the kairix features stdout parses as JSON with a flags key
    And the kairix features command exits with code 0
