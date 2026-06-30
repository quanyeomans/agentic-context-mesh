Feature: SLO harness — one command measures perf and affordance
  As an engineer driving the Agent Performance & Affordance Wave
  I want one command that reports latency, recall quality, and breadcrumb completeness
  So that I can claim directional improvement against measured baselines

  Scenario: An engineer runs the harness and sees all three SLO dimensions
    Given the synthetic measurement workload
    When the engineer runs the SLO harness as JSON
    Then the report includes cold and warm latency for every most-used command
    And the report includes fact-recall quality for the labelled suite
    And every agent-facing result carries a resolvable source breadcrumb

  Scenario: The harness reports latency at single and high concurrency
    Given the synthetic measurement workload
    When the engineer runs the SLO harness at concurrency 4 as JSON
    Then the latency table covers concurrency 1 and concurrency 4
