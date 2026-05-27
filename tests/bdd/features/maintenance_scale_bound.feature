@maintenance @scale_bound
Feature: maintenance tick bounds the orphan-prune scan per call
  As an operator running kairix against a production-scale knowledge store
  (millions of content_vectors rows + millions of documents rows)
  I want each maintenance tick to scan and prune at most a configured
  number of orphan rows
  So that the worker doesn't saturate disk I/O the moment it boots
  on a big database — the historical bug was an unbounded LEFT JOIN
  that scanned both tables sequentially on every tick.

  Drain semantics: if a backlog exceeds the cap, subsequent ticks
  pick up the remainder. The soft-delete table is idempotent on
  (hash, seq) so the multi-tick drain produces the same end state
  as a single unbounded sweep would have — just without the disk
  saturation.

  @happy_path
  Scenario: tick prunes a bounded number of rows per call
    Given the database holds 2500 orphan content_vectors rows
    And the maintenance scheduler is configured with a per-tick cap of 1000
    When one maintenance tick runs
    Then the tick reports at most 1000 rows pruned
    And the remaining orphans stay in content_vectors for the next tick
