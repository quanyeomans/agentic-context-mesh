Feature: kairix doctor agent validates configured scopes against disk
  As an operator running kairix in production
  I want `kairix doctor agent` to re-validate each agent's scope on disk
  So that I see drift (missing dirs, stale memory, ambiguous overlap) before agents do.

  Scenario: every configured agent has populated recent surfaces
    Given a configured agent "agent-alpha" with a populated recent surface
    When the operator runs the doctor agent CLI with the --all flag
    Then the doctor CLI exits with status 0
    And stdout from the doctor CLI mentions "overall=ok"

  Scenario: an agent has a missing surface directory
    Given a configured agent "agent-beta" whose surface path does not exist
    When the operator runs the doctor agent CLI with the --all flag
    Then the doctor CLI exits with status 1
    And stdout from the doctor CLI carries the "path missing" issue
    And stdout from the doctor CLI suggests "kairix onboard agent"

  Scenario: doctor agent --name returns a single AgentHealth envelope
    Given a configured agent "agent-alpha" with a populated recent surface
    When the operator runs the doctor agent CLI for "agent-alpha" with --json
    Then the doctor CLI exits with status 0
    And the JSON envelope carries an "agent" key with the expected fields
