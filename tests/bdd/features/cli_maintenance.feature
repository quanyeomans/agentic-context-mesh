Feature: maintenance subcommand
  As a kairix operator
  I want the `kairix maintenance analyze` subcommand
  So that I can refresh SQLite query-planner statistics on demand and
  via the same warm-up + scheduler bookkeeping the worker uses

  Scenario: Fresh database runs ANALYZE on warm-up
    Given a kairix index with documents but no sqlite_stat1 rows
    When the warm step ensure_sqlite_stats runs
    Then the step reports detail "ANALYZE complete"
    And the index now has sqlite_stat1 rows present

  Scenario: Database with recent stats skips ANALYZE on warm-up
    Given a kairix index with sqlite_stat1 already populated
    When the warm step ensure_sqlite_stats runs
    Then the step reports detail "stats already present, skipped"
    And the step reports elapsed_ms equal to zero

  Scenario: Operator runs kairix maintenance analyze
    Given a kairix process configured with FakePaths and a seeded index
    When the operator runs `kairix maintenance analyze` with valid input
    Then the command exits 0 and prints the expected envelope
