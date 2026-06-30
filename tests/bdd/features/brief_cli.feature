Feature: kairix brief CLI
  As an operator running per-agent session briefings
  I want `kairix brief <agent>` to brief any agent I have configured and surface clear errors
  So that every onboarded agent — not just a fixed built-in list — can read its own briefing.

  Scenario: A configured agent is briefed
    Given the brief CLI has agent "agent-alpha" configured with a memory surface
    When the operator briefs the configured agent "agent-alpha"
    Then the brief CLI exits with status 0
    And stderr does not report an invalid agent

  Scenario: An agent with no configured surface is rejected with a helpful stderr
    Given the brief CLI has agent "ghost" configured with no surfaces
    When the operator briefs the configured agent "ghost"
    Then the brief CLI exits with status 1
    And stderr names the rejected agent "ghost"
    And stderr explains how to configure the agent

  Scenario: A missing agent argument produces a usage error
    When the operator runs the brief CLI with no arguments
    Then the brief CLI exits with status 2
