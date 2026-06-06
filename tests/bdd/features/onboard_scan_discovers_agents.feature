Feature: kairix onboard scan discovers agents on disk
  As an operator setting up kairix on a workstation
  I want `kairix onboard scan` to discover agents under my memory root
  So that I can paste a config block instead of writing one by hand.

  Scenario: scan finds two agents with mixed harnesses and emits a YAML block
    Given an empty memory root and an empty workspace root
    And an agent named "agent-alpha" with a CLAUDE.md and three markdown files in the memory root
    And an agent named "agent-beta" with a Board.md and five markdown files in the memory root
    And an "agent-alpha" workspace subdir exists in the workspace root
    When the operator runs the onboard scan CLI with the YAML mode
    Then the onboard scan CLI exits with status 0
    And the YAML output carries an "agents" top-level key
    And the YAML output names the agent "agent-alpha" with harness "claude-code"
    And the YAML output names the agent "agent-beta" with harness "generic"

  Scenario: onboard agent for unknown agent surfaces an actionable error
    Given an empty memory root and an empty workspace root
    When the operator runs the onboard agent CLI for "nonexistent"
    Then the onboard scan CLI exits with status 1
    And stderr from the onboard scan CLI names the missing agent
