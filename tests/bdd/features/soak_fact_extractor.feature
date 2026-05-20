Feature: Soak — fact-extractor pipeline holds together under sustained load
  As a kairix operator running a long-lived knowledge store
  I want fact ingest, federated search, and consolidation to keep their
  latency / memory / consistency budgets across hours of operation
  So that production regressions surface in nightly soak instead of in deployment

  # Plan B-parity Week 4 Stream C — soak BDD coverage for the
  # fact-extractor pipeline shipped in Capability #1-#4. Scenarios
  # are gated on the ``KAIRIX_SOAK=1`` environment variable; the
  # default test suite collects them but skips at runtime.
  # See ``tests/bdd/steps/soak_fact_extractor_steps.py`` for the
  # step bindings and budget thresholds.

  Scenario: Continuous-ingest soak keeps latency and memory bounded
    Given a synthetic conversation generator producing one chat every 30 seconds
    And a fresh fact store and null extractor wired through the ingest use case
    When the operator runs continuous ingest for the configured soak budget
    Then per-ingest latency stays within the documented soak budget
    And the fact store grows by fact count not by turn count
    And the SQLite write-ahead-log stays bounded across the run
    And resident memory growth stays under one hundred megabytes

  Scenario: Concurrent ingest and query meet read-your-writes consistency
    Given a fresh fact store seeded with a small baseline corpus
    When the operator runs one ingest per minute alongside ten queries per second
    Then no deadlock or store error is observed across the run
    And every freshly ingested fact is visible to a subsequent query

  Scenario: Large fact store keeps federated search and conflict lookup fast
    Given a fact store pre-loaded with one hundred thousand synthetic facts
    When the operator runs the federated search probe against the store
    Then federated search median latency stays under four hundred milliseconds
    And find_conflicts median latency stays under fifty milliseconds
