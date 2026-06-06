Feature: agent scope drives memory + workspace path resolution
  As an operator running per-agent briefings + episodic memory writes
  I want every callsite to follow the configured AgentScope surfaces
  So that the briefings + writes land where I declared in kairix.config.yaml.

  Scenario: brief reads from every configured surface for an agent
    Given an agent with surfaces at the memory dir and the workspace dir
    And a memory log file in both surfaces for today
    When the brief source fetcher reads memory logs for the agent
    Then the result contains the marker from both surfaces

  Scenario: classify routes episodic memory to the agent's writable_path
    Given an agent with a workspace surface and a memory surface
    When the classify router resolves an episodic target for the agent
    Then the resolved path is under the memory surface

  Scenario: fallback agent uses agent_defaults synthesis
    Given no explicit agent entry in config but an agent_defaults memory root
    When the brief source fetcher reads memory logs for the fallback agent
    Then the result includes the synthesised surface content
    And a synthesis warning names the fallback agent
