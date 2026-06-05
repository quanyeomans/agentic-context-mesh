Feature: kairix caches — operator inspection of W-B TTL LRU stats
  As a kairix operator
  I want to inspect the in-process cache stats added by Workstream B
  So that I can see which caches are paying off after a load test or dogfood session

  Scenario: the caches command renders a table listing every W-B cache
    Given the kairix process is freshly started
    When the operator runs the caches command
    Then the report lists every cache by name
    And each cache row shows size, hits, misses, evictions, and hit_rate percent

  Scenario: the caches command emits a JSON envelope under --json
    Given the kairix process is freshly started
    When the operator runs the caches command with the json flag
    Then stdout is a valid JSON object with a caches array
